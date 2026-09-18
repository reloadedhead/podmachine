from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from podmachine.artwork import ensure_channel_artwork
from podmachine.channels import list_channels
from podmachine.config import AppConfig
from podmachine.db import connect
from podmachine.downloader import download_audio
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos
from podmachine.requeue import requeue_stale_failures
from podmachine.retention import apply_retention
from podmachine.tagger import fetch_thumbnail, tag_audio_file
from podmachine.youtube import fetch_channel_avatar_url, fetch_channel_feed

logger = logging.getLogger("podmachine.scheduler")

JOB_ID = "podmachine-cycle"


def run_cycle(
    config: AppConfig,
    db_path: Path,
    fetch_fn=fetch_channel_feed,
    download_fn=download_audio,
    tag_fn=tag_audio_file,
    sleep_fn=time.sleep,
    avatar_url_fn=fetch_channel_avatar_url,
    avatar_bytes_fn=fetch_thumbnail,
    retention_fn=apply_retention,
    requeue_fn=requeue_stale_failures,
) -> dict:
    conn = connect(db_path)
    try:
        channels = list_channels(conn)
        poll_results = poll_all_channels(conn, channels, fetch=fetch_fn, sleep_fn=sleep_fn)

        artwork_dir = config.data_dir / "artwork"
        for channel in channels:
            ensure_channel_artwork(
                conn, channel, artwork_dir, avatar_url_fn=avatar_url_fn, fetch_bytes_fn=avatar_bytes_fn
            )

        requeued_count = requeue_fn(conn)

        media_dir = config.data_dir / "media"
        channel_names = {c.slug: c.name for c in channels}
        process_results = process_pending_videos(
            conn, media_dir, channel_names, download_fn=download_fn, tag_fn=tag_fn, sleep_fn=sleep_fn
        )

        deleted_count = 0
        for channel in channels:
            effective_retention = channel.retention or config.retention
            deleted_count += retention_fn(conn, channel, effective_retention, media_dir)
    finally:
        conn.close()

    for result in poll_results:
        if result.error:
            logger.warning("Poll failed for %s: %s", result.slug, result.error)
    for result in process_results:
        if not result.success:
            logger.warning("Download failed for %s: %s", result.video_id, result.error)

    logger.info(
        "Cycle complete: %d channel(s) polled, %d stale failure(s) requeued, %d video(s) processed, "
        "%d episode(s) deleted (retention)",
        len(poll_results),
        requeued_count,
        len(process_results),
        deleted_count,
    )
    return {
        "poll_results": [asdict(r) for r in poll_results],
        "process_results": [asdict(r) for r in process_results],
        "retention_deleted": deleted_count,
        "requeued": requeued_count,
    }


def start_scheduler(config: AppConfig, db_path: Path) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_cycle,
        "interval",
        minutes=config.poll_interval_minutes,
        args=[config, db_path],
        id=JOB_ID,
        next_run_time=datetime.now(),
    )
    scheduler.start()
    return scheduler
