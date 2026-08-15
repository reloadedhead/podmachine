from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable

from podmachine.config import ChannelConfig
from podmachine.db import utcnow_iso
from podmachine.youtube import VideoEntry, fetch_channel_feed

logger = logging.getLogger("podmachine.poller")

FetchFn = Callable[[str], list[VideoEntry]]


@dataclass
class PollResult:
    slug: str
    baseline_established_now: bool
    new_pending: int
    new_skipped_shorts: int
    error: str | None = None


def poll_channel(
    conn: sqlite3.Connection,
    channel: ChannelConfig,
    fetch: FetchFn = fetch_channel_feed,
) -> PollResult:
    row = conn.execute(
        "SELECT baseline_established FROM channel_state WHERE slug = ?",
        (channel.slug,),
    ).fetchone()
    baseline_established = bool(row["baseline_established"]) if row else False

    try:
        entries = fetch(channel.id)
    except Exception as exc:
        logger.exception("Failed to poll channel %s", channel.slug)
        return PollResult(channel.slug, False, 0, 0, error=str(exc))

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
        if entry.is_short:
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
) -> list[PollResult]:
    return [poll_channel(conn, channel, fetch=fetch) for channel in channels]


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
