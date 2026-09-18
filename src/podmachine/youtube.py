from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import requests
import yt_dlp

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
USER_AGENT = "podmachine-poller/0.1"

NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


@dataclass
class VideoEntry:
    video_id: str
    title: str
    published_at: str
    url: str
    is_short: bool


def fetch_channel_feed(channel_id: str, timeout: float = 10.0) -> list[VideoEntry]:
    response = requests.get(
        FEED_URL,
        params={"channel_id": channel_id},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_feed(response.text)


def parse_feed(xml_text: str) -> list[VideoEntry]:
    root = ET.fromstring(xml_text)
    entries = []
    for entry in root.findall("atom:entry", NAMESPACES):
        video_id = entry.findtext("yt:videoId", namespaces=NAMESPACES)
        title = entry.findtext("atom:title", namespaces=NAMESPACES)
        published_at = entry.findtext("atom:published", namespaces=NAMESPACES)
        link_el = entry.find("atom:link", NAMESPACES)
        url = link_el.get("href", "") if link_el is not None else ""

        if not video_id or not title or not published_at:
            continue

        entries.append(
            VideoEntry(
                video_id=video_id,
                title=title,
                published_at=published_at,
                url=url,
                is_short="/shorts/" in url,
            )
        )
    return entries


def fetch_channel_name(channel_id: str, timeout: float = 10.0) -> str | None:
    """The channel's display name, from the same RSS feed used to poll for
    videos — its root <title> is the channel name, not a video title."""
    response = requests.get(
        FEED_URL,
        params={"channel_id": channel_id},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_channel_name(response.text)


def parse_channel_name(xml_text: str) -> str | None:
    root = ET.fromstring(xml_text)
    return root.findtext("atom:title", namespaces=NAMESPACES)


def fetch_channel_avatar_url(channel_id: str) -> str | None:
    # extract_flat + playlist_items='0' fetches only the channel's own
    # metadata (title, thumbnails) without enumerating any of its videos —
    # verified this takes well under a second regardless of channel size.
    ydl_opts = {
        "extract_flat": "in_playlist",
        "playlist_items": "0",
        "quiet": True,
        "no_warnings": True,
    }
    url = f"https://www.youtube.com/channel/{channel_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return select_avatar_url(info.get("thumbnails") or [])


def select_avatar_url(thumbnails: list[dict[str, Any]]) -> str | None:
    """Pick the channel avatar out of yt-dlp's thumbnail list. The channel
    banner is included at several wide (~6:1) resolutions; the avatar is
    the square one — verified against multiple real channels.
    """
    square = [t for t in thumbnails if t.get("width") and t.get("height") and t["width"] == t["height"]]
    if not square:
        return None
    return max(square, key=lambda t: t["width"]).get("url")
