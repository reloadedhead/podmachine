from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from podmachine.config import ChannelConfig, RetentionConfig
from podmachine.db import utcnow_iso

logger = logging.getLogger("podmachine.retention")


def apply_retention(
    conn: sqlite3.Connection,
    channel: ChannelConfig,
    retention: RetentionConfig,
    media_dir: Path,
) -> int:
    """Apply the configured retention policy to one channel's episodes.
    Returns the number of episodes deleted.
    """
    if retention.strategy == "none":
        return 0
    if retention.strategy == "count":
        return _apply_count_strategy(conn, channel, retention.keep_latest)

    # age/size: planned but not implemented yet — see docs/retention.md.
    logger.warning(
        "Retention strategy %r configured for channel %s is not implemented yet; skipping",
        retention.strategy,
        channel.slug,
    )
    return 0


def _apply_count_strategy(conn: sqlite3.Connection, channel: ChannelConfig, keep_latest: int) -> int:
    rows = conn.execute(
        "SELECT video_id, file_path FROM videos WHERE channel_slug = ? AND status = 'done' "
        "ORDER BY published_at DESC",
        (channel.slug,),
    ).fetchall()

    to_delete = rows[keep_latest:]
    for row in to_delete:
        delete_episode(conn, row["video_id"], row["file_path"])
    return len(to_delete)


def delete_episode(conn: sqlite3.Connection, video_id: str, file_path: str | None) -> None:
    # Tombstone, never remove the row: poll_channel's dedup check is
    # status-agnostic ("SELECT video_id FROM videos WHERE channel_slug = ?"),
    # so a removed row would look newly-discovered again on the next poll
    # and get redownloaded. See docs/retention.md.
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            # Genuinely unusual for our own writable /data — log and still
            # tombstone the row rather than retry this forever.
            logger.warning("Failed to remove file for %s: %s", video_id, file_path, exc_info=True)

    conn.execute(
        "UPDATE videos SET status = 'deleted', file_path = NULL, file_size = NULL, deleted_at = ? "
        "WHERE video_id = ?",
        (utcnow_iso(), video_id),
    )
    conn.commit()
    logger.info("Deleted episode %s (retention policy)", video_id)
