from __future__ import annotations

import json
import sqlite3

from podmachine.config import RetentionConfig

DEFAULT_RETENTION_KEY = "default_retention"


def get_default_retention(conn: sqlite3.Connection) -> RetentionConfig:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (DEFAULT_RETENTION_KEY,)).fetchone()
    if row is None:
        return RetentionConfig()
    return RetentionConfig(**json.loads(row["value"]))


def set_default_retention(conn: sqlite3.Connection, retention: RetentionConfig) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (DEFAULT_RETENTION_KEY, retention.model_dump_json()),
    )
    conn.commit()


def import_default_retention_from_config_if_unset(conn: sqlite3.Connection, retention: RetentionConfig) -> None:
    """One-time bootstrap, same pattern as channels.import_channels_from_config_if_empty:
    config.yaml's top-level `retention:` block seeds this the first time it's
    unset, then config.yaml's retention field is ignored — the DB becomes the
    source of truth so the admin UI can change the app-wide default without a
    container restart."""
    row = conn.execute("SELECT 1 FROM settings WHERE key = ?", (DEFAULT_RETENTION_KEY,)).fetchone()
    if row is not None:
        return
    set_default_retention(conn, retention)
