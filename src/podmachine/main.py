from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from podmachine.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("podmachine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.config = config
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
