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

# Additive columns layered onto `videos` after the initial release. Applied
# via ALTER TABLE rather than baked into SCHEMA so existing deployments keep
# their data instead of needing the volume wiped on every schema change.
VIDEO_COLUMNS = {
    "file_path": "TEXT",
    "file_size": "INTEGER",
    "downloaded_at": "TEXT",
    "error_message": "TEXT",
    "description": "TEXT",
    "thumbnail_url": "TEXT",
    "duration_seconds": "INTEGER",
    "deleted_at": "TEXT",
    "last_attempt_at": "TEXT",
    "long_range_retry_count": "INTEGER NOT NULL DEFAULT 0",
}

CHANNEL_STATE_COLUMNS = {
    "consecutive_poll_failures": "INTEGER NOT NULL DEFAULT 0",
    "backed_off_until": "TEXT",
    "last_poll_error": "TEXT",
    "avatar_path": "TEXT",
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _ensure_columns(conn, "videos", VIDEO_COLUMNS)
        _ensure_columns(conn, "channel_state", CHANNEL_STATE_COLUMNS)
        conn.commit()
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
