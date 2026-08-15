from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

from podmachine.config import ChannelConfig

logger = logging.getLogger("podmachine.feed")

DEFAULT_ITUNES_CATEGORY = "Society & Culture"


def _itunes_image_url(url: str | None) -> str | None:
    """feedgen requires itunes:image URLs to end in .jpg/.png, but YouTube
    thumbnail URLs commonly carry a sizing query string after the
    extension (e.g. '...sd2.jpg?sqp=...'). Strip it so the still-valid
    image URL passes feedgen's check instead of raising and taking the
    whole feed down.
    """
    if not url:
        return None
    stripped = url.split("?", 1)[0]
    return stripped if stripped.lower().endswith((".jpg", ".png")) else None


def build_channel_feed(conn: sqlite3.Connection, channel: ChannelConfig, base_url: str) -> str:
    rows = conn.execute(
        "SELECT video_id, title, published_at, description, thumbnail_url, "
        "duration_seconds, file_size FROM videos "
        "WHERE channel_slug = ? AND status = 'done' "
        "ORDER BY published_at DESC",
        (channel.slug,),
    ).fetchall()

    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.title(channel.name)
    fg.link(href=f"{base_url}/feeds/{channel.slug}.xml", rel="self")
    fg.link(href=f"https://www.youtube.com/channel/{channel.id}", rel="alternate")
    fg.description(f"Audio episodes from the {channel.name} YouTube channel.")
    fg.language("en")
    fg.podcast.itunes_author(channel.name)
    fg.podcast.itunes_category(DEFAULT_ITUNES_CATEGORY)
    fg.podcast.itunes_explicit("no")

    channel_row = conn.execute("SELECT avatar_path FROM channel_state WHERE slug = ?", (channel.slug,)).fetchone()
    if channel_row and channel_row["avatar_path"]:
        # The real channel avatar, self-hosted (see artwork.py) — always
        # ends in .jpg, so it passes feedgen's itunes:image check as-is.
        feed_image = f"{base_url}/artwork/{channel.slug}.jpg"
    else:
        # Avatar not fetched yet (or fetch failed): fall back to borrowing
        # the latest episode's thumbnail so the feed isn't imageless.
        feed_image = next((row["thumbnail_url"] for row in rows if row["thumbnail_url"]), None)

    if feed_image:
        itunes_feed_image = _itunes_image_url(feed_image)
        if itunes_feed_image:
            fg.podcast.itunes_image(itunes_feed_image)
        # The plain RSS <image> isn't suffix-restricted, so the original
        # URL (with any sizing query string) is fine to use as-is here.
        fg.image(url=feed_image, title=channel.name, link=f"{base_url}/feeds/{channel.slug}.xml")

    for row in rows:
        # add_entry() defaults to order='prepend', which would silently
        # reverse the newest-first ordering already established by the
        # SQL query above.
        fe = fg.add_entry(order="append")
        fe.id(row["video_id"])
        fe.guid(row["video_id"], permalink=False)
        fe.title(row["title"])
        summary = row["description"] or row["title"]
        fe.description(summary)
        fe.podcast.itunes_summary(summary)
        fe.pubDate(_parse_iso(row["published_at"]))
        fe.enclosure(
            f"{base_url}/media/{channel.slug}/{row['video_id']}.mp3",
            str(row["file_size"] or 0),
            "audio/mpeg",
        )
        if row["duration_seconds"]:
            fe.podcast.itunes_duration(int(row["duration_seconds"]))
        episode_image = _itunes_image_url(row["thumbnail_url"])
        if episode_image:
            fe.podcast.itunes_image(episode_image)

    return fg.rss_str(pretty=True).decode("utf-8")


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
