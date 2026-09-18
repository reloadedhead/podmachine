from __future__ import annotations

import logging
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from podmachine.db import utcnow_iso
from podmachine.downloader import DownloadResult, download_audio
from podmachine.tagger import tag_audio_file

logger = logging.getLogger("podmachine.processor")

DownloadFn = Callable[[str, str, Path], DownloadResult]
TagFn = Callable[..., None]
SleepFn = Callable[[float], None]

# YouTube-side 403s are often transient (confirmed by hand during Phase 2
# testing: an immediate manual retry succeeded). Retry a few times with
# exponential backoff before giving up.
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_BASE_SECONDS = 2

# Small randomized gap between sequential downloads, same reasoning as the
# poller's inter-channel jitter: avoid bursty, bot-like request patterns.
DOWNLOAD_JITTER_MIN_SECONDS = 1.0
DOWNLOAD_JITTER_MAX_SECONDS = 3.0


@dataclass
class ProcessResult:
    video_id: str
    channel_slug: str
    success: bool
    error: str | None = None


def process_pending_videos(
    conn: sqlite3.Connection,
    media_dir: Path,
    channel_names: dict[str, str],
    download_fn: DownloadFn = download_audio,
    tag_fn: TagFn = tag_audio_file,
    sleep_fn: SleepFn = time.sleep,
) -> list[ProcessResult]:
    rows = conn.execute(
        "SELECT video_id, channel_slug, title, published_at FROM videos WHERE status = 'pending'"
    ).fetchall()

    results = []
    for i, row in enumerate(rows):
        results.append(_process_one(conn, row, media_dir, channel_names, download_fn, tag_fn, sleep_fn))
        if i < len(rows) - 1:
            sleep_fn(random.uniform(DOWNLOAD_JITTER_MIN_SECONDS, DOWNLOAD_JITTER_MAX_SECONDS))
    return results


def _download_with_retry(
    video_id: str,
    channel_slug: str,
    media_dir: Path,
    download_fn: DownloadFn,
    sleep_fn: SleepFn,
) -> DownloadResult:
    result = None
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        result = download_fn(video_id, channel_slug, media_dir)
        if result.success or attempt == MAX_DOWNLOAD_ATTEMPTS:
            return result
        backoff = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
        logger.warning(
            "Download attempt %d/%d failed for %s: %s (retrying in %ds)",
            attempt,
            MAX_DOWNLOAD_ATTEMPTS,
            video_id,
            result.error,
            backoff,
        )
        sleep_fn(backoff)
    return result


def _process_one(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    media_dir: Path,
    channel_names: dict[str, str],
    download_fn: DownloadFn,
    tag_fn: TagFn,
    sleep_fn: SleepFn,
) -> ProcessResult:
    video_id = row["video_id"]
    channel_slug = row["channel_slug"]

    conn.execute("UPDATE videos SET status = 'downloading' WHERE video_id = ?", (video_id,))
    conn.commit()

    result = _download_with_retry(video_id, channel_slug, media_dir, download_fn, sleep_fn)
    now = utcnow_iso()

    if not result.success:
        conn.execute(
            "UPDATE videos SET status = 'failed', error_message = ?, last_attempt_at = ? WHERE video_id = ?",
            (result.error, now, video_id),
        )
        conn.commit()
        logger.warning("Download failed for %s after %d attempts: %s", video_id, MAX_DOWNLOAD_ATTEMPTS, result.error)
        return ProcessResult(video_id, channel_slug, False, result.error)

    info = result.info or {}
    try:
        tag_fn(
            result.file_path,
            title=row["title"],
            channel_name=channel_names.get(channel_slug, channel_slug),
            published_at=row["published_at"],
            description=info.get("description") or "",
            thumbnail_url=info.get("thumbnail"),
        )
    except Exception:
        # Audio is already downloaded and usable; a tagging failure shouldn't
        # discard that work, so the episode still gets marked done.
        logger.exception("Tagging failed for %s (audio downloaded OK)", video_id)

    # Re-stat rather than trust result.file_size: tagging (embedded artwork,
    # description) changes the file size after the download step measured it.
    file_size = result.file_path.stat().st_size

    conn.execute(
        "UPDATE videos SET status = 'done', file_path = ?, file_size = ?, "
        "downloaded_at = ?, last_attempt_at = ?, error_message = NULL, description = ?, "
        "thumbnail_url = ?, duration_seconds = ? WHERE video_id = ?",
        (
            str(result.file_path),
            file_size,
            now,
            now,
            info.get("description") or None,
            info.get("thumbnail"),
            info.get("duration"),
            video_id,
        ),
    )
    conn.commit()
    return ProcessResult(video_id, channel_slug, True)


def retry_video(conn: sqlite3.Connection, video_id: str) -> None:
    """Manually requeue one 'failed' episode, same transition requeue.py
    applies automatically on its long-range schedule. The next process run
    (button or scheduled cycle) picks it back up."""
    conn.execute(
        "UPDATE videos SET status = 'pending', error_message = NULL WHERE video_id = ?",
        (video_id,),
    )
    conn.commit()
