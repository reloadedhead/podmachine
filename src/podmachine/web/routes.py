from __future__ import annotations

import sqlite3
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from podmachine.channels import (
    DuplicateChannelError,
    add_channel,
    delete_channel,
    get_channel,
    list_channels,
    slugify,
    unique_slug,
    update_channel_retention,
)
from podmachine.config import ChannelConfig, RetentionConfig
from podmachine.db import connect
from podmachine.poller import poll_all_channels
from podmachine.processor import process_pending_videos, retry_video
from podmachine.queries import channel_status_rows, channel_videos
from podmachine.retention import delete_episode
from podmachine.web.auth import require_admin
from podmachine.youtube import fetch_channel_name

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _find_channel(conn: sqlite3.Connection, slug: str) -> ChannelConfig:
    channel = get_channel(conn, slug)
    if channel is None:
        raise HTTPException(status_code=404, detail="Unknown channel")
    return channel


def _find_video(conn: sqlite3.Connection, slug: str, video_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT video_id, status, file_path FROM videos WHERE video_id = ? AND channel_slug = ?",
        (video_id, slug),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown video")
    return row


# Statuses eligible for the "Queue" action: never downloaded (baseline,
# skipped_short) or previously deleted. Not pending/downloading (already
# queued) or done (already downloaded) — those aren't offered the button.
# 'failed' has its own dedicated Retry action instead.
QUEUEABLE_STATUSES = {"baseline", "skipped_short", "deleted"}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channels = channel_status_rows(conn, list_channels(conn))
    finally:
        conn.close()
    return templates.TemplateResponse(request, "dashboard.html", {"channels": channels, "error": None})


@router.get("/partials/channels", response_class=HTMLResponse)
def channels_partial(request: Request) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channels = channel_status_rows(conn, list_channels(conn))
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": channels, "error": None})


@router.get("/channels/{slug}", response_class=HTMLResponse)
def channel_detail(request: Request, slug: str) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channel = _find_channel(conn, slug)
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(
        request, "channel_detail.html", {"channel": channel, "videos": videos, "error": None}
    )


@router.get("/partials/channels/{slug}/videos", response_class=HTMLResponse)
def channel_videos_partial(request: Request, slug: str) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channel = _find_channel(conn, slug)
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/video_table.html", {"channel": channel, "videos": videos})


@router.post("/channels", response_class=HTMLResponse)
def action_add_channel(
    request: Request,
    channel_id: str = Form(...),
    name: str = Form(""),
    slug: str = Form(""),
) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        error = None
        try:
            resolved_name = name.strip()
            if not resolved_name:
                try:
                    resolved_name = fetch_channel_name(channel_id)
                except requests.RequestException:
                    resolved_name = None
                if not resolved_name:
                    raise ValueError(
                        f"Couldn't fetch a channel name for {channel_id!r} — enter one manually"
                    )

            resolved_slug = slug.strip() or unique_slug(conn, slugify(resolved_name))

            add_channel(conn, ChannelConfig(id=channel_id, name=resolved_name, slug=resolved_slug))
        except (DuplicateChannelError, ValidationError, ValueError) as exc:
            error = str(exc)
        channels = channel_status_rows(conn, list_channels(conn))
    finally:
        conn.close()
    status_code = 400 if error else 200
    return templates.TemplateResponse(
        request,
        "partials/channel_table.html",
        {"channels": channels, "error": error},
        status_code=status_code,
    )


@router.post("/channels/{slug}/delete", response_class=HTMLResponse)
def action_delete_channel(request: Request, slug: str) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        _find_channel(conn, slug)
        delete_channel(conn, slug)
        channels = channel_status_rows(conn, list_channels(conn))
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": channels, "error": None})


@router.post("/channels/{slug}/retention", response_class=HTMLResponse)
def action_update_retention(
    request: Request,
    slug: str,
    strategy: str = Form(""),
    keep_latest: str = Form(""),
) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channel = _find_channel(conn, slug)
        error = None
        try:
            if not strategy:
                retention = None
            else:
                retention = RetentionConfig(
                    strategy=strategy, keep_latest=int(keep_latest) if keep_latest else None
                )
            update_channel_retention(conn, slug, retention)
            channel = _find_channel(conn, slug)
        except ValidationError as exc:
            error = str(exc)
    finally:
        conn.close()
    status_code = 400 if error else 200
    return templates.TemplateResponse(
        request,
        "partials/channel_meta.html",
        {"channel": channel, "error": error},
        status_code=status_code,
    )


@router.post("/channels/{slug}/videos/{video_id}/delete", response_class=HTMLResponse)
def action_delete_video(request: Request, slug: str, video_id: str) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channel = _find_channel(conn, slug)
        row = _find_video(conn, slug, video_id)
        delete_episode(conn, video_id, row["file_path"])
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/video_table.html", {"channel": channel, "videos": videos})


@router.post("/channels/{slug}/videos/{video_id}/retry", response_class=HTMLResponse)
def action_retry_video(request: Request, slug: str, video_id: str) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channel = _find_channel(conn, slug)
        _find_video(conn, slug, video_id)
        retry_video(conn, video_id)
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/video_table.html", {"channel": channel, "videos": videos})


@router.post("/channels/{slug}/videos/{video_id}/queue", response_class=HTMLResponse)
def action_queue_video(request: Request, slug: str, video_id: str) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channel = _find_channel(conn, slug)
        row = _find_video(conn, slug, video_id)
        if row["status"] not in QUEUEABLE_STATUSES:
            raise HTTPException(status_code=400, detail=f"Video is {row['status']!r}, not queueable")
        retry_video(conn, video_id)
        videos = channel_videos(conn, slug)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/video_table.html", {"channel": channel, "videos": videos})


@router.post("/actions/poll", response_class=HTMLResponse)
def action_poll(request: Request) -> HTMLResponse:
    conn = connect(request.app.state.db_path)
    try:
        channels = list_channels(conn)
        poll_all_channels(conn, channels)
        rows = channel_status_rows(conn, channels)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": rows, "error": None})


@router.post("/actions/process", response_class=HTMLResponse)
def action_process(request: Request) -> HTMLResponse:
    config = request.app.state.config
    conn = connect(request.app.state.db_path)
    try:
        channels = list_channels(conn)
        media_dir = config.data_dir / "media"
        channel_names = {c.slug: c.name for c in channels}
        process_pending_videos(conn, media_dir, channel_names)
        rows = channel_status_rows(conn, channels)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "partials/channel_table.html", {"channels": rows, "error": None})
