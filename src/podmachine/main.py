from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from podmachine.config import load_config
from podmachine.db import connect, init_db
from podmachine.feed import build_channel_feed
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos
from podmachine.scheduler import run_cycle, start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("podmachine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
    app.state.db_path = config.data_dir / "podmachine.sqlite3"
    init_db(app.state.db_path)
    logger.info(
        "Loaded config: %d channel(s), polling every %d min",
        len(config.channels),
        config.poll_interval_minutes,
    )
    app.state.scheduler = start_scheduler(config, app.state.db_path)
    yield
    app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="podmachine", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    config = app.state.config
    return {
        "status": "ok",
        "channels": len(config.channels),
        "poll_interval_minutes": config.poll_interval_minutes,
    }


@app.get("/channels")
def list_channels() -> dict:
    config = app.state.config
    conn = connect(app.state.db_path)
    try:
        channels = []
        for channel in config.channels:
            state_row = conn.execute(
                "SELECT baseline_established, last_polled_at, consecutive_poll_failures, "
                "backed_off_until, last_poll_error FROM channel_state WHERE slug = ?",
                (channel.slug,),
            ).fetchone()
            counts_rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM videos WHERE channel_slug = ? GROUP BY status",
                (channel.slug,),
            ).fetchall()
            counts = {row["status"]: row["n"] for row in counts_rows}
            channels.append(
                {
                    "slug": channel.slug,
                    "name": channel.name,
                    "id": channel.id,
                    "baseline_established": bool(state_row["baseline_established"]) if state_row else False,
                    "last_polled_at": state_row["last_polled_at"] if state_row else None,
                    "consecutive_poll_failures": state_row["consecutive_poll_failures"] if state_row else 0,
                    "backed_off_until": state_row["backed_off_until"] if state_row else None,
                    "last_poll_error": state_row["last_poll_error"] if state_row else None,
                    "video_counts": counts,
                }
            )
        return {"channels": channels}
    finally:
        conn.close()


@app.post("/poll")
def poll_now() -> dict:
    config = app.state.config
    conn = connect(app.state.db_path)
    try:
        results = poll_all_channels(conn, config.channels)
        return {"results": [asdict(r) for r in results]}
    finally:
        conn.close()


@app.post("/process")
def process_now() -> dict:
    config = app.state.config
    conn = connect(app.state.db_path)
    try:
        media_dir = config.data_dir / "media"
        channel_names = {c.slug: c.name for c in config.channels}
        results = process_pending_videos(conn, media_dir, channel_names)
        return {"results": [asdict(r) for r in results]}
    finally:
        conn.close()


@app.post("/run")
def run_now() -> dict:
    return run_cycle(app.state.config, app.state.db_path)


@app.api_route("/feeds/{channel_slug}.xml", methods=["GET", "HEAD"])
def get_feed(channel_slug: str) -> Response:
    config = app.state.config
    channel = next((c for c in config.channels if c.slug == channel_slug), None)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel")

    conn = connect(app.state.db_path)
    try:
        xml = build_channel_feed(conn, channel, config.base_url)
    finally:
        conn.close()
    return Response(content=xml, media_type="application/rss+xml")


@app.api_route("/media/{channel_slug}/{filename}", methods=["GET", "HEAD"])
def get_media_file(channel_slug: str, filename: str) -> FileResponse:
    media_root = (app.state.config.data_dir / "media").resolve()
    file_path = (media_root / channel_slug / filename).resolve()
    if media_root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(file_path, media_type="audio/mpeg")
