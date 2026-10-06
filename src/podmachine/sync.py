"""Manual "Sync now": poll every channel, then download whatever is pending,
in a background thread, while keeping a progress snapshot the admin UI can
poll.

The two steps are the same ones the separate "Poll now" and "Process now"
buttons used to run; this module just runs them back to back off the request
thread and records where it is. State lives in memory only — a restart
forgets a sync in flight, which is fine: the episodes it had already
discovered stay 'pending' in the DB for the next cycle.
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from pathlib import Path

from podmachine.channels import list_channels
from podmachine.config import AppConfig
from podmachine.db import connect, utcnow_iso
from podmachine.downloader import download_audio
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos
from podmachine.tagger import tag_audio_file
from podmachine.youtube import fetch_channel_feed

logger = logging.getLogger("podmachine.sync")

# How long a finished sync's summary keeps showing on the dashboard before
# it falls back to idle on its own (it can also be dismissed).
SUMMARY_TTL_SECONDS = 5 * 60

# Per-channel states shown as badges in the channel table while a sync runs.
WAITING = "waiting"
CHECKING = "checking"
CHECKED = "checked"
QUEUED = "queued"
DOWNLOADING = "downloading"
DOWNLOADED = "downloaded"
FAILED = "failed"

ACTIVE_STATES = {CHECKING, DOWNLOADING}
GOOD_STATES = {CHECKED, DOWNLOADED}


def _fresh_state() -> dict:
    return {
        "phase": "idle",  # idle | polling | processing | done
        "current": "",  # channel name while polling, episode title while processing
        "done": 0,  # finished items in the current phase
        "total": 0,
        "channels": {},  # slug -> {"state": ..., "label": ...}
        "poll_errors": [],  # [{"name": ..., "error": ...}]
        "downloaded": 0,
        "failed_downloads": 0,
        "channels_with_new": 0,
        "fatal_error": None,
        "finished_at": None,
        "finished_monotonic": None,
    }


class SyncManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = _fresh_state()

    # --- reading -----------------------------------------------------

    def snapshot(self) -> dict:
        """A copy of the current progress, with the derived fields the
        templates use (running, percent, outcome)."""
        with self._lock:
            state = copy.deepcopy(self._state)
        finished = state.pop("finished_monotonic")
        if state["phase"] == "done" and finished is not None and time.monotonic() - finished > SUMMARY_TTL_SECONDS:
            state = _fresh_state()
            state.pop("finished_monotonic")
        state["running"] = state["phase"] in ("polling", "processing")
        state["percent"] = _percent(state)
        state["has_errors"] = bool(state["poll_errors"] or state["failed_downloads"] or state["fatal_error"])
        state["summary_title"], state["summary_detail"] = _summary(state)
        return state

    def is_running(self) -> bool:
        with self._lock:
            return self._state["phase"] in ("polling", "processing")

    # --- control -----------------------------------------------------

    def start(self, config: AppConfig, db_path: Path) -> bool:
        """Kick off a sync in the background. Returns False (and does nothing)
        if one is already running."""
        if not self._begin():
            return False
        threading.Thread(
            target=self._run, args=(config, db_path), daemon=True, name="podmachine-sync"
        ).start()
        return True

    def dismiss(self) -> None:
        """Clear a finished sync's summary. A sync in flight can't be dismissed."""
        with self._lock:
            if self._state["phase"] == "done":
                self._state = _fresh_state()

    def _begin(self) -> bool:
        with self._lock:
            if self._state["phase"] in ("polling", "processing"):
                return False
            self._state = _fresh_state()
            self._state["phase"] = "polling"
            return True

    # --- the sync itself ----------------------------------------------

    def _run(
        self,
        config: AppConfig,
        db_path: Path,
        fetch_fn=fetch_channel_feed,
        download_fn=download_audio,
        tag_fn=tag_audio_file,
        sleep_fn=time.sleep,
    ) -> None:
        try:
            conn = connect(db_path)
            try:
                channels = list_channels(conn)
                names = {c.slug: c.name for c in channels}
                with self._lock:
                    self._state["total"] = len(channels)
                    self._state["channels"] = {c.slug: _badge(WAITING) for c in channels}

                poll_all_channels(
                    conn,
                    channels,
                    fetch=fetch_fn,
                    sleep_fn=sleep_fn,
                    progress=lambda channel, i, total, result: self._on_poll(channel, i, result),
                )

                pending = {
                    row["channel_slug"]: row["n"]
                    for row in conn.execute(
                        "SELECT channel_slug, COUNT(*) AS n FROM videos WHERE status = 'pending' "
                        "GROUP BY channel_slug"
                    )
                }
                total_pending = sum(pending.values())
                if total_pending:
                    self._start_processing(pending, total_pending)
                    remaining = dict(pending)
                    failed_slugs: set[str] = set()
                    process_pending_videos(
                        conn,
                        config.data_dir / "media",
                        names,
                        download_fn=download_fn,
                        tag_fn=tag_fn,
                        sleep_fn=sleep_fn,
                        progress=lambda row, i, total, result: self._on_process(
                            row, i, total, result, remaining, failed_slugs
                        ),
                    )
            finally:
                conn.close()
        except Exception as exc:
            logger.exception("Manual sync failed")
            with self._lock:
                self._state["fatal_error"] = str(exc)
        finally:
            with self._lock:
                self._state["phase"] = "done"
                self._state["finished_at"] = utcnow_iso()
                self._state["finished_monotonic"] = time.monotonic()
                self._state["current"] = ""

    def _on_poll(self, channel, index: int, result) -> None:
        with self._lock:
            if result is None:
                self._state["current"] = channel.name
                self._state["channels"][channel.slug] = _badge(CHECKING)
                return
            self._state["done"] = index + 1
            if result.error:
                self._state["channels"][channel.slug] = _badge(FAILED, "couldn't check")
                self._state["poll_errors"].append({"name": channel.name, "error": result.error})
            elif result.skipped_backoff:
                # Left alone on purpose (circuit breaker): no override, so the
                # table keeps showing its regular "backed off" badge.
                self._state["channels"].pop(channel.slug, None)
            else:
                self._state["channels"][channel.slug] = _badge(CHECKED)

    def _start_processing(self, pending: dict[str, int], total: int) -> None:
        with self._lock:
            self._state["phase"] = "processing"
            self._state["done"] = 0
            self._state["total"] = total
            self._state["current"] = ""
            self._state["channels_with_new"] = len(pending)
            channels = {}
            for slug in self._state["channels"]:
                channels[slug] = _badge(QUEUED) if slug in pending else self._state["channels"][slug]
            # Pending episodes can belong to a channel the poll skipped (a
            # manual Queue on a backed-off channel); give those a badge too.
            for slug in pending:
                channels[slug] = _badge(QUEUED)
            self._state["channels"] = channels

    def _on_process(self, row, index: int, total: int, result, remaining, failed_slugs) -> None:
        slug = row["channel_slug"]
        with self._lock:
            if result is None:
                self._state["current"] = row["title"]
                self._state["channels"][slug] = _badge(DOWNLOADING)
                return
            self._state["done"] = index + 1
            remaining[slug] -= 1
            if result.success:
                self._state["downloaded"] += 1
            else:
                self._state["failed_downloads"] += 1
                failed_slugs.add(slug)
            if remaining[slug] == 0:
                self._state["channels"][slug] = (
                    _badge(FAILED, "download failed") if slug in failed_slugs else _badge(DOWNLOADED)
                )


def _badge(state: str, label: str | None = None) -> dict:
    return {"state": state, "label": label or state}


def _percent(state: dict) -> int:
    """Overall progress: checking the channels is the first half, downloading
    the second. Both halves count the item in flight as half done so the bar
    is already moving while the first item runs."""
    total = state["total"]
    if state["phase"] == "idle":
        return 0
    if state["phase"] == "done":
        return 100
    fraction = min((state["done"] + 0.5) / total, 1.0) if total else 0.0
    if state["phase"] == "polling":
        return round(fraction * 50)
    return round(50 + fraction * 50)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _summary(state: dict) -> tuple[str, str]:
    """Headline + detail line for a finished sync."""
    if state["phase"] != "done":
        return "", ""
    if state["fatal_error"]:
        return "Sync stopped unexpectedly", state["fatal_error"]

    downloaded = _plural(state["downloaded"], "new episode") + " downloaded"
    if not state["has_errors"]:
        if not state["downloaded"]:
            return "Sync complete", "No new episodes."
        return "Sync complete", f"{downloaded} from {_plural(state['channels_with_new'], 'channel')}."

    errors = len(state["poll_errors"]) + state["failed_downloads"]
    parts = []
    if len(state["poll_errors"]) == 1:
        parts.append(f"{state['poll_errors'][0]['name']} couldn’t be checked")
    elif state["poll_errors"]:
        parts.append(f"{len(state['poll_errors'])} channels couldn’t be checked")
    if state["failed_downloads"]:
        parts.append(f"{_plural(state['failed_downloads'], 'download')} failed")
    if state["downloaded"]:
        parts.append(downloaded)
    return f"Sync finished with {_plural(errors, 'error')}", ". ".join(parts) + "."
