from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from podmachine.db import connect
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos
from podmachine.queries import channel_status_rows, channel_videos
from podmachine.web.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _find_channel(request: Request, slug: str):
    channel = next((c for c in request.app.state.config.channels if c.slug == slug), None)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel")
    return channel


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    config = request.app.state.config
    conn = connect(request.app.state.db_path)
    try:
        channels = channel_status_rows(conn, config.channels)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "dashboard.html", {"channels": channels})


@router.get("/partials/channels", response_class=HTMLResponse)
def channels_partial(request: Request) -> HTMLResponse:
    config = request.app.state.config
    conn = connect(request.app.state.db_path)
    try:
        channels = channel_status_rows(conn, config.channels)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": channels})


@router.get("/channels/{slug}", response_class=HTMLResponse)
def channel_detail(request: Request, slug: str) -> HTMLResponse:
    channel = _find_channel(request, slug)
    conn = connect(request.app.state.db_path)
    try:
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "channel_detail.html", {"channel": channel, "videos": videos})


@router.get("/partials/channels/{slug}/videos", response_class=HTMLResponse)
def channel_videos_partial(request: Request, slug: str) -> HTMLResponse:
    channel = _find_channel(request, slug)
    conn = connect(request.app.state.db_path)
    try:
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/video_table.html", {"channel": channel, "videos": videos})


@router.post("/actions/poll", response_class=HTMLResponse)
def action_poll(request: Request) -> HTMLResponse:
    config = request.app.state.config
    conn = connect(request.app.state.db_path)
    try:
        poll_all_channels(conn, config.channels)
        channels = channel_status_rows(conn, config.channels)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": channels})


@router.post("/actions/process", response_class=HTMLResponse)
def action_process(request: Request) -> HTMLResponse:
    config = request.app.state.config
    conn = connect(request.app.state.db_path)
    try:
        media_dir = config.data_dir / "media"
        channel_names = {c.slug: c.name for c in config.channels}
        process_pending_videos(conn, media_dir, channel_names)
        channels = channel_status_rows(conn, config.channels)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": channels})
