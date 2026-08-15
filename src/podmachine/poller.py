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
from podmachine.youtube import VideoEntry, fetch_channel_feed

logger = logging.getLogger("podmachine.poller")

FetchFn = Callable[[str], list[VideoEntry]]
SleepFn = Callable[[float], None]

# A channel that fails to poll this many times in a row (bad channel ID,
# deleted channel, persistent network issue) gets left alone for a while
# instead of being hammered every cycle.
CIRCUIT_BREAKER_THRESHOLD = 5
CIRCUIT_BREAKER_BACKOFF_HOURS = 2

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

    try:
        entries = fetch(channel.id)
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
    sleep_fn: SleepFn = time.sleep,
) -> list[PollResult]:
    results = []
    for i, channel in enumerate(channels):
        results.append(poll_channel(conn, channel, fetch=fetch))
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


def _record_poll_success(conn: sqlite3.Connection, channel: ChannelConfig) -> None:
    conn.execute(
        "UPDATE channel_state SET consecutive_poll_failures = 0, backed_off_until = NULL, "
        "last_poll_error = NULL WHERE slug = ?",
        (channel.slug,),
    )
    conn.commit()


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
