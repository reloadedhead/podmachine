from __future__ import annotations

import json
import re
import sqlite3

from podmachine.config import ChannelConfig, RetentionConfig


class DuplicateChannelError(ValueError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "channel"


def unique_slug(conn: sqlite3.Connection, base_slug: str) -> str:
    """base_slug, or base_slug-2, -3, ... if it's already taken — slugs are
    the channel's primary key and feed into feed/media URLs, so two
    channels can never share one."""
    slug = base_slug
    suffix = 2
    while conn.execute("SELECT 1 FROM channels WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    return slug


def _row_to_channel(row: sqlite3.Row) -> ChannelConfig:
    retention = RetentionConfig(**json.loads(row["retention_json"])) if row["retention_json"] else None
    return ChannelConfig(id=row["id"], name=row["name"], slug=row["slug"], retention=retention)


def list_channels(conn: sqlite3.Connection) -> list[ChannelConfig]:
    rows = conn.execute("SELECT slug, id, name, retention_json FROM channels ORDER BY name").fetchall()
    return [_row_to_channel(row) for row in rows]


def get_channel(conn: sqlite3.Connection, slug: str) -> ChannelConfig | None:
    row = conn.execute(
        "SELECT slug, id, name, retention_json FROM channels WHERE slug = ?", (slug,)
    ).fetchone()
    return _row_to_channel(row) if row else None


def add_channel(conn: sqlite3.Connection, channel: ChannelConfig) -> None:
    existing = conn.execute(
        "SELECT 1 FROM channels WHERE id = ? OR slug = ?", (channel.id, channel.slug)
    ).fetchone()
    if existing:
        raise DuplicateChannelError(f"A channel with id {channel.id!r} or slug {channel.slug!r} already exists")
    conn.execute(
        "INSERT INTO channels (slug, id, name, retention_json) VALUES (?, ?, ?, ?)",
        (channel.slug, channel.id, channel.name, channel.retention.model_dump_json() if channel.retention else None),
    )
    conn.commit()


def update_channel_retention(conn: sqlite3.Connection, slug: str, retention: RetentionConfig | None) -> None:
    conn.execute(
        "UPDATE channels SET retention_json = ? WHERE slug = ?",
        (retention.model_dump_json() if retention else None, slug),
    )
    conn.commit()


def delete_channel(conn: sqlite3.Connection, slug: str) -> None:
    conn.execute("DELETE FROM channels WHERE slug = ?", (slug,))
    conn.commit()


def import_channels_from_config_if_empty(conn: sqlite3.Connection, channels: list[ChannelConfig]) -> None:
    """One-time bootstrap: config.yaml's channels list seeds this table the
    first time it's empty, then config.yaml's channels field is ignored —
    the DB becomes the source of truth so the admin UI can add/edit/remove
    channels without a container restart."""
    count = conn.execute("SELECT COUNT(*) AS n FROM channels").fetchone()["n"]
    if count > 0:
        return
    for channel in channels:
        conn.execute(
            "INSERT INTO channels (slug, id, name, retention_json) VALUES (?, ?, ?, ?)",
            (channel.slug, channel.id, channel.name, channel.retention.model_dump_json() if channel.retention else None),
        )
    conn.commit()
