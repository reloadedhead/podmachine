from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from podmachine.config import load_config
from podmachine.db import connect, init_db
from podmachine.feed import build_channel_feed
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos
from podmachine.queries import channel_status_rows
from podmachine.scheduler import run_cycle, start_scheduler
from podmachine.web.routes import router as admin_router

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
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
app.include_router(admin_router)


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
        return {"channels": channel_status_rows(conn, config.channels)}
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


@app.api_route("/artwork/{channel_slug}.jpg", methods=["GET", "HEAD"])
def get_artwork(channel_slug: str) -> FileResponse:
    artwork_root = (app.state.config.data_dir / "artwork").resolve()
    file_path = (artwork_root / f"{channel_slug}.jpg").resolve()
    if artwork_root not in file_path.parents or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(file_path, media_type="image/jpeg")
