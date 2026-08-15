from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from podmachine.db import utcnow_iso
from podmachine.downloader import DownloadResult, download_audio
from podmachine.tagger import tag_audio_file

logger = logging.getLogger("podmachine.processor")

DownloadFn = Callable[[str, str, Path], DownloadResult]
TagFn = Callable[..., None]


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
) -> list[ProcessResult]:
    rows = conn.execute(
        "SELECT video_id, channel_slug, title, published_at FROM videos WHERE status = 'pending'"
    ).fetchall()

    return [_process_one(conn, row, media_dir, channel_names, download_fn, tag_fn) for row in rows]


def _process_one(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    media_dir: Path,
    channel_names: dict[str, str],
    download_fn: DownloadFn,
    tag_fn: TagFn,
) -> ProcessResult:
    video_id = row["video_id"]
    channel_slug = row["channel_slug"]

    conn.execute("UPDATE videos SET status = 'downloading' WHERE video_id = ?", (video_id,))
    conn.commit()

    result = download_fn(video_id, channel_slug, media_dir)

    if not result.success:
        conn.execute(
            "UPDATE videos SET status = 'failed', error_message = ? WHERE video_id = ?",
            (result.error, video_id),
        )
        conn.commit()
        logger.warning("Download failed for %s: %s", video_id, result.error)
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
        "downloaded_at = ?, error_message = NULL WHERE video_id = ?",
        (str(result.file_path), file_size, utcnow_iso(), video_id),
    )
    conn.commit()
    return ProcessResult(video_id, channel_slug, True)
