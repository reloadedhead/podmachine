from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger("podmachine.downloader")

# Bytes/sec. Audio-only files are small, so this isn't about bandwidth —
# it's about not looking like a bot hammering YouTube as fast as possible.
DOWNLOAD_RATE_LIMIT_BYTES_PER_SEC = 2_000_000


@dataclass
class DownloadResult:
    video_id: str
    success: bool
    file_path: Path | None = None
    file_size: int | None = None
    info: dict[str, Any] | None = None
    error: str | None = None


class _YtDlpLogAdapter:
    def debug(self, msg: str) -> None:
        logger.debug(msg)

    def info(self, msg: str) -> None:
        logger.debug(msg)

    def warning(self, msg: str) -> None:
        logger.warning(msg)

    def error(self, msg: str) -> None:
        logger.error(msg)


def download_audio(video_id: str, channel_slug: str, media_dir: Path) -> DownloadResult:
    out_dir = media_dir / channel_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(out_dir / f"{video_id}.%(ext)s")

    # Explicit cachedir rather than yt-dlp's default (under $HOME) — under
    # the non-root container user, $HOME may not be writable, but /data
    # (media_dir's parent) always is.
    cache_dir = media_dir.parent / ".yt-dlp-cache"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "5",
            }
        ],
        "ratelimit": DOWNLOAD_RATE_LIMIT_BYTES_PER_SEC,
        "cachedir": str(cache_dir),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _YtDlpLogAdapter(),
    }

    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        logger.warning("Download failed for %s: %s", video_id, exc)
        return DownloadResult(video_id=video_id, success=False, error=str(exc))

    mp3_path = out_dir / f"{video_id}.mp3"
    if not mp3_path.exists():
        return DownloadResult(
            video_id=video_id,
            success=False,
            error="Expected output file missing after yt-dlp reported success",
        )

    return DownloadResult(
        video_id=video_id,
        success=True,
        file_path=mp3_path,
        file_size=mp3_path.stat().st_size,
        info=info,
    )
