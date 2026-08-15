from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI

from podmachine.config import load_config
from podmachine.db import connect, init_db
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos

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
    yield


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
                "SELECT baseline_established, last_polled_at FROM channel_state WHERE slug = ?",
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
