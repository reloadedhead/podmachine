from __future__ import annotations

import sqlite3

from podmachine.config import ChannelConfig

# Order episodes are shown in on the dashboard/detail pages: most recently
# discovered first, regardless of status.
VIDEO_LIST_COLUMNS = (
    "video_id, channel_slug, title, published_at, status, discovered_at, "
    "downloaded_at, error_message, duration_seconds"
)


def channel_status_rows(conn: sqlite3.Connection, channels: list[ChannelConfig]) -> list[dict]:
    """Per-channel status + video counts, shared by the JSON /channels endpoint
    and the HTML admin dashboard so the two don't drift."""
    rows = []
    for channel in channels:
        state_row = conn.execute(
            "SELECT baseline_established, last_polled_at, consecutive_poll_failures, "
            "backed_off_until, last_poll_error, avatar_path FROM channel_state WHERE slug = ?",
            (channel.slug,),
        ).fetchone()
        counts_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM videos WHERE channel_slug = ? GROUP BY status",
            (channel.slug,),
        ).fetchall()
        counts = {row["status"]: row["n"] for row in counts_rows}
        rows.append(
            {
                "slug": channel.slug,
                "name": channel.name,
                "id": channel.id,
                "baseline_established": bool(state_row["baseline_established"]) if state_row else False,
                "last_polled_at": state_row["last_polled_at"] if state_row else None,
                "consecutive_poll_failures": state_row["consecutive_poll_failures"] if state_row else 0,
                "backed_off_until": state_row["backed_off_until"] if state_row else None,
                "last_poll_error": state_row["last_poll_error"] if state_row else None,
                "has_avatar": bool(state_row["avatar_path"]) if state_row else False,
                "video_counts": counts,
            }
        )
    return rows


def channel_videos(conn: sqlite3.Connection, channel_slug: str, limit: int = 200) -> list[sqlite3.Row]:
    """Most recently discovered episodes for one channel, for the admin detail page."""
    return conn.execute(
        f"SELECT {VIDEO_LIST_COLUMNS} FROM videos WHERE channel_slug = ? "
        "ORDER BY discovered_at DESC LIMIT ?",
        (channel_slug, limit),
    ).fetchall()
