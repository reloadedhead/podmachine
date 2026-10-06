from __future__ import annotations

import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from podmachine.config import ChannelConfig
from podmachine.db import utcnow_iso
from podmachine.youtube import VideoEntry, fetch_channel_feed, fetch_playlist_entries

logger = logging.getLogger("podmachine.poller")

FetchFn = Callable[[str], list[VideoEntry]]
SleepFn = Callable[[float], None]
# Called with (channel, index, total, result) around each channel's poll:
# result is None just before the channel is polled, the PollResult after.
PollProgressFn = Callable[["ChannelConfig", int, int, "PollResult | None"], None]

# A channel that fails to poll this many times in a row (bad channel ID,
# deleted channel, persistent network issue) gets left alone for a while
# instead of being hammered every cycle. Kept short because YouTube's feed
# endpoint also fails transiently (see youtube.fetch_channel_feed), and a
# long backoff turns a brief outage into hours of missed uploads.
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_BACKOFF_HOURS = 1

# Small randomized gap between polling consecutive channels so requests to
# YouTube don't arrive in a perfectly synchronized burst.
POLL_JITTER_MIN_SECONDS = 1.0
POLL_JITTER_MAX_SECONDS = 4.0


@dataclass
class PollResult:
    slug: str
    baseline_established_now: bool
    new_pending: int
    new_skipped_shorts: int
    skipped_backoff: bool = False
    error: str | None = None


def poll_channel(
    conn: sqlite3.Connection,
    channel: ChannelConfig,
    fetch: FetchFn = fetch_channel_feed,
    fetch_playlist: FetchFn = fetch_playlist_entries,
) -> PollResult:
    row = conn.execute(
        "SELECT baseline_established, backed_off_until FROM channel_state WHERE slug = ?",
        (channel.slug,),
    ).fetchone()
    baseline_established = bool(row["baseline_established"]) if row else False

    if row and row["backed_off_until"]:
        backed_off_until = datetime.fromisoformat(row["backed_off_until"])
        if datetime.now(timezone.utc) < backed_off_until:
            logger.info("Skipping poll for %s: backed off until %s", channel.slug, row["backed_off_until"])
            return PollResult(channel.slug, False, 0, 0, skipped_backoff=True)
        # Backoff has expired: start from a clean slate so one more failure
        # doesn't immediately re-trigger a full backoff, and the admin UI
        # stops showing "backed off" and the stale error.
        _reset_poll_failure_state(conn, channel)

    try:
        entries = (fetch_playlist if channel.is_playlist else fetch)(channel.id)
    except Exception as exc:
        logger.exception("Failed to poll channel %s", channel.slug)
        _record_poll_failure(conn, channel, str(exc))
        return PollResult(channel.slug, False, 0, 0, error=str(exc))

    _record_poll_success(conn, channel)

    now = utcnow_iso()

    if not baseline_established:
        # First time we've seen this channel: record everything currently
        # listed as 'baseline' so nothing gets queued for download. Only
        # videos discovered on later polls should ever become 'pending'.
        for entry in entries:
            _insert_video(conn, channel.slug, entry, status="baseline", now=now)
        conn.execute(
            "INSERT INTO channel_state (slug, channel_id, baseline_established, last_polled_at) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(slug) DO UPDATE SET baseline_established = 1, last_polled_at = excluded.last_polled_at",
            (channel.slug, channel.id, now),
        )
        conn.commit()
        return PollResult(channel.slug, True, 0, 0)

    known_ids = {
        r["video_id"]
        for r in conn.execute(
            "SELECT video_id FROM videos WHERE channel_slug = ?", (channel.slug,)
        )
    }

    new_pending = 0
    new_skipped_shorts = 0
    for entry in entries:
        if entry.video_id in known_ids:
            continue
        # A playlist is a curated selection, so Shorts in it are wanted.
        if entry.is_short and not channel.is_playlist:
            _insert_video(conn, channel.slug, entry, status="skipped_short", now=now)
            new_skipped_shorts += 1
        else:
            _insert_video(conn, channel.slug, entry, status="pending", now=now)
            new_pending += 1

    conn.execute(
        "UPDATE channel_state SET last_polled_at = ? WHERE slug = ?",
        (now, channel.slug),
    )
    conn.commit()

    return PollResult(channel.slug, False, new_pending, new_skipped_shorts)


def poll_all_channels(
    conn: sqlite3.Connection,
    channels: list[ChannelConfig],
    fetch: FetchFn = fetch_channel_feed,
    sleep_fn: SleepFn = time.sleep,
    progress: PollProgressFn | None = None,
    fetch_playlist: FetchFn = fetch_playlist_entries,
) -> list[PollResult]:
    results = []
    for i, channel in enumerate(channels):
        if progress:
            progress(channel, i, len(channels), None)
        result = poll_channel(conn, channel, fetch=fetch, fetch_playlist=fetch_playlist)
        results.append(result)
        if progress:
            progress(channel, i, len(channels), result)
        if i < len(channels) - 1:
            sleep_fn(random.uniform(POLL_JITTER_MIN_SECONDS, POLL_JITTER_MAX_SECONDS))
    return results


def _record_poll_failure(conn: sqlite3.Connection, channel: ChannelConfig, error_message: str) -> None:
    row = conn.execute(
        "SELECT consecutive_poll_failures FROM channel_state WHERE slug = ?", (channel.slug,)
    ).fetchone()
    failures = (row["consecutive_poll_failures"] if row else 0) + 1

    backed_off_until = None
    if failures >= CIRCUIT_BREAKER_THRESHOLD:
        backed_off_until = (datetime.now(timezone.utc) + timedelta(hours=CIRCUIT_BREAKER_BACKOFF_HOURS)).isoformat()
        logger.warning(
            "Channel %s hit %d consecutive poll failures; backing off until %s",
            channel.slug,
            failures,
            backed_off_until,
        )

    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established, consecutive_poll_failures, "
        "backed_off_until, last_poll_error) VALUES (?, ?, 0, ?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET consecutive_poll_failures = excluded.consecutive_poll_failures, "
        "backed_off_until = excluded.backed_off_until, last_poll_error = excluded.last_poll_error",
        (channel.slug, channel.id, failures, backed_off_until, error_message),
    )
    conn.commit()


def _reset_poll_failure_state(conn: sqlite3.Connection, channel: ChannelConfig) -> None:
    conn.execute(
        "UPDATE channel_state SET consecutive_poll_failures = 0, backed_off_until = NULL, "
        "last_poll_error = NULL WHERE slug = ?",
        (channel.slug,),
    )
    conn.commit()


def _record_poll_success(conn: sqlite3.Connection, channel: ChannelConfig) -> None:
    _reset_poll_failure_state(conn, channel)


def _insert_video(
    conn: sqlite3.Connection,
    channel_slug: str,
    entry: VideoEntry,
    status: str,
    now: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO videos "
        "(video_id, channel_slug, title, published_at, status, discovered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entry.video_id, channel_slug, entry.title, entry.published_at, status, now),
    )
