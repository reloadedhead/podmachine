from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable

from podmachine.config import ChannelConfig
from podmachine.tagger import fetch_thumbnail
from podmachine.youtube import fetch_channel_avatar_url

logger = logging.getLogger("podmachine.artwork")

AvatarUrlFn = Callable[[str], str | None]
FetchBytesFn = Callable[[str], bytes]


def ensure_channel_artwork(
    conn: sqlite3.Connection,
    channel: ChannelConfig,
    artwork_dir: Path,
    avatar_url_fn: AvatarUrlFn = fetch_channel_avatar_url,
    fetch_bytes_fn: FetchBytesFn = fetch_thumbnail,
) -> None:
    row = conn.execute("SELECT avatar_path FROM channel_state WHERE slug = ?", (channel.slug,)).fetchone()
    existing_path = row["avatar_path"] if row else None
    if existing_path and Path(existing_path).is_file():
        return

    try:
        avatar_url = avatar_url_fn(channel.id)
        if not avatar_url:
            logger.info("No avatar found for channel %s", channel.slug)
            return
        image_bytes = fetch_bytes_fn(avatar_url)
    except Exception:
        # Best-effort metadata — never let an avatar fetch failure break
        # the poll/download cycle. Simply retried next cycle.
        logger.warning("Failed to fetch channel avatar for %s", channel.slug, exc_info=True)
        return

    artwork_dir.mkdir(parents=True, exist_ok=True)
    file_path = artwork_dir / f"{channel.slug}.jpg"
    file_path.write_bytes(image_bytes)

    # Upsert rather than UPDATE: this can legitimately run before any poll
    # has created a channel_state row for this channel, in which case a
    # plain UPDATE would silently affect zero rows.
    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established, avatar_path) VALUES (?, ?, 0, ?) "
        "ON CONFLICT(slug) DO UPDATE SET avatar_path = excluded.avatar_path",
        (channel.slug, channel.id, str(file_path)),
    )
    conn.commit()
    logger.info("Cached channel avatar for %s", channel.slug)
