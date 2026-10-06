from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
USER_AGENT = "podmachine-poller/0.1"

logger = logging.getLogger("podmachine.youtube")

# YouTube's RSS endpoint intermittently returns 404 (and occasionally 5xx)
# for valid channel IDs; a retry seconds later normally succeeds. Retry
# these, and connection errors, a few times before reporting a failure so a
# blip doesn't count towards the poller's circuit breaker.
FEED_MAX_ATTEMPTS = 3
FEED_RETRY_BASE_SECONDS = 2
RETRYABLE_STATUS_CODES = frozenset({404, 429}) | frozenset(range(500, 600))

# PL: user playlists, OL: albums, UU: a channel's uploads, FL: favourites.
PLAYLIST_ID_PREFIXES = ("PL", "OL", "UU", "FL")

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


def fetch_channel_feed(
    channel_id: str,
    timeout: float = 10.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[VideoEntry]:
    for attempt in range(1, FEED_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                FEED_URL,
                params={"channel_id": channel_id},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            return parse_feed(response.text)
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            status = exc.response.status_code if isinstance(exc, requests.HTTPError) else None
            retryable = not isinstance(exc, requests.HTTPError) or status in RETRYABLE_STATUS_CODES
            if not retryable or attempt == FEED_MAX_ATTEMPTS:
                raise
            backoff = FEED_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Feed fetch attempt %d/%d failed for %s: %s (retrying in %ds)",
                attempt,
                FEED_MAX_ATTEMPTS,
                channel_id,
                exc,
                backoff,
            )
            sleep_fn(backoff)
    raise AssertionError("unreachable")  # pragma: no cover


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


def parse_source(raw: str) -> tuple[Literal["channel", "playlist"], str]:
    """Turn what the admin pasted (a bare ID, or a channel/playlist URL) into
    (source_type, youtube_id). Handles (@name) aren't resolved — they need a
    page fetch to map to a UC… ID."""
    value = raw.strip()
    if "://" in value or value.startswith(("www.", "youtube.com", "m.youtube.com")):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        playlist_ids = parse_qs(parsed.query).get("list")
        if playlist_ids:
            return "playlist", playlist_ids[0]
        match = re.search(r"/channel/(UC[\w-]+)", parsed.path)
        if match:
            return "channel", match.group(1)
        # /show/VL<id> and /browse/VL<id> pages wrap a playlist ID in "VL".
        match = re.search(r"/(?:show|browse)/VL([\w-]+)", parsed.path)
        if match:
            return "playlist", match.group(1)
        raise ValueError(f"Couldn't find a channel ID or playlist in {raw!r} — paste a /channel/UC… or playlist URL")
    if value.startswith("UC"):
        return "channel", value
    if value.startswith(PLAYLIST_ID_PREFIXES):
        return "playlist", value
    if value.startswith("VL") and value[2:].startswith(PLAYLIST_ID_PREFIXES):
        return "playlist", value[2:]
    raise ValueError(f"{raw!r} doesn't look like a channel ID (UC…) or playlist ID (PL…)")


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={playlist_id}"


def _flat_playlist_info(playlist_id: str, items: str | None = None) -> dict[str, Any]:
    ydl_opts: dict[str, Any] = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True}
    if items is not None:
        ydl_opts["playlist_items"] = items
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(playlist_url(playlist_id), download=False) or {}


def fetch_playlist_entries(playlist_id: str) -> list[VideoEntry]:
    """Every video in the playlist, via yt-dlp's flat listing rather than
    the RSS feed: playlist RSS only returns the first 15 items in playlist
    order, and most playlists append new videos at the end, so new uploads
    would never show up there."""
    return parse_playlist_entries(_flat_playlist_info(playlist_id))


def parse_playlist_entries(info: dict[str, Any], now: str | None = None) -> list[VideoEntry]:
    # Flat entries usually carry no upload date, so discovery time stands in
    # until the real date is filled in from the download's metadata.
    fallback_published = now or datetime.now(timezone.utc).isoformat()
    entries = []
    for entry in info.get("entries") or []:
        video_id = entry.get("id")
        title = entry.get("title")
        # Deleted/private videos stay listed with placeholder titles and
        # can't be downloaded.
        if not video_id or not title or title in ("[Deleted video]", "[Private video]"):
            continue
        timestamp = entry.get("timestamp") or entry.get("release_timestamp")
        published_at = (
            datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else fallback_published
        )
        entries.append(
            VideoEntry(
                video_id=video_id,
                title=title,
                published_at=published_at,
                url=entry.get("url") or f"https://www.youtube.com/watch?v={video_id}",
                # A playlist is already a curated selection — don't second-guess it.
                is_short=False,
            )
        )
    return entries


def fetch_playlist_name(playlist_id: str) -> str | None:
    return _flat_playlist_info(playlist_id, items="0").get("title")


def fetch_playlist_avatar_url(playlist_id: str) -> str | None:
    """The playlist owner's channel avatar — playlists have no avatar of
    their own, only a thumbnail borrowed from one of their videos."""
    owner_id = _flat_playlist_info(playlist_id, items="0").get("channel_id")
    return fetch_channel_avatar_url(owner_id) if owner_id else None


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
