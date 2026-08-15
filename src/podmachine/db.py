from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_state (
    slug TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    baseline_established INTEGER NOT NULL DEFAULT 0,
    last_polled_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    channel_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    status TEXT NOT NULL,
    discovered_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_channel_slug ON videos(channel_slug);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
