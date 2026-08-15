from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

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
