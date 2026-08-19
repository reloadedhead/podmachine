from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("podmachine.requeue")

# A video that exhausts all 3 in-cycle retry attempts (processor.py) is
# marked 'failed' and never looked at again by default. Some failures are
# permanent (deleted video, region lock); others are a snapshot of an
# in-progress, partial YouTube-side rollout that can resolve itself for a
# given video within days (confirmed by hand: an unrelated video succeeded
# seconds after a failing one, ruling out a blanket IP block). Rather than
# requiring someone to notice and manually reprocess, periodically give
# 'failed' videos another shot, capped so a truly-dead video doesn't retry
# forever.
LONG_RANGE_RETRY_INTERVAL_HOURS = 24
MAX_LONG_RANGE_RETRIES = 5


def requeue_stale_failures(conn: sqlite3.Connection) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LONG_RANGE_RETRY_INTERVAL_HOURS)).isoformat()

    rows = conn.execute(
        "SELECT video_id FROM videos WHERE status = 'failed' "
        "AND long_range_retry_count < ? "
        "AND (last_attempt_at IS NULL OR last_attempt_at < ?)",
        (MAX_LONG_RANGE_RETRIES, cutoff),
    ).fetchall()

    for row in rows:
        conn.execute(
            "UPDATE videos SET status = 'pending', long_range_retry_count = long_range_retry_count + 1 "
            "WHERE video_id = ?",
            (row["video_id"],),
        )
        logger.info("Requeuing stale failure %s for another attempt", row["video_id"])
    conn.commit()

    return len(rows)
