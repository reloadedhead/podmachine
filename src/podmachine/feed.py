from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from feedgen.feed import FeedGenerator

from podmachine.config import ChannelConfig

DEFAULT_ITUNES_CATEGORY = "Society & Culture"


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

    feed_image = next((row["thumbnail_url"] for row in rows if row["thumbnail_url"]), None)
    if feed_image:
        fg.podcast.itunes_image(feed_image)
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
        if row["thumbnail_url"]:
            fe.podcast.itunes_image(row["thumbnail_url"])

    return fg.rss_str(pretty=True).decode("utf-8")


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
