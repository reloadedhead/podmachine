from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import requests
from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1, TPE2

logger = logging.getLogger("podmachine.tagger")


def fetch_thumbnail(url: str, timeout: float = 10.0) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def tag_audio_file(
    file_path: Path,
    title: str,
    channel_name: str,
    published_at: str,
    description: str = "",
    thumbnail_url: str | None = None,
    fetch_thumbnail_fn: Callable[[str], bytes] = fetch_thumbnail,
) -> None:
    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TIT2")
    tags.add(TIT2(encoding=3, text=title))
    tags.delall("TPE1")
    tags.add(TPE1(encoding=3, text=channel_name))
    tags.delall("TPE2")
    tags.add(TPE2(encoding=3, text=channel_name))
    tags.delall("TALB")
    tags.add(TALB(encoding=3, text=channel_name))
    tags.delall("TDRC")
    tags.add(TDRC(encoding=3, text=published_at[:10]))

    tags.delall("COMM")
    if description:
        tags.add(COMM(encoding=3, lang="eng", desc="desc", text=description[:1000]))

    tags.delall("APIC")
    if thumbnail_url:
        try:
            image_bytes = fetch_thumbnail_fn(thumbnail_url)
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=image_bytes))
        except Exception:
            logger.warning("Failed to embed artwork for %s", file_path.name)

    tags.save(file_path)
