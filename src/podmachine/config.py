from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path(os.environ.get("PODMACHINE_CONFIG", "/config/config.yaml"))

# Apple's top-level iTunes/Apple Podcasts categories (subcategories aren't
# supported yet — see the README). feedgen itself doesn't validate
# itunes:category against this list, so it's enforced here instead, and
# mirrored as a SQL CHECK constraint in db.py so the two can't drift.
ITUNES_CATEGORIES = (
    "Arts",
    "Business",
    "Comedy",
    "Education",
    "Fiction",
    "Government",
    "Health & Fitness",
    "History",
    "Kids & Family",
    "Leisure",
    "Music",
    "News",
    "Religion & Spirituality",
    "Science",
    "Society & Culture",
    "Sports",
    "Technology",
    "True Crime",
    "TV & Film",
)


class RetentionConfig(BaseModel):
    # Only "count" is implemented; "age" and "size" are planned — see
    # docs/retention.md. Default is "none": auto-deleting files is
    # destructive enough that it should be opt-in, not a silent new
    # behavior after an upgrade.
    strategy: Literal["none", "count", "age", "size"] = "none"
    keep_latest: int | None = None
    max_age_days: int | None = None
    max_total_mb: int | None = None
    keep_latest_minimum: int = 3

    @model_validator(mode="after")
    def check_required_fields(self) -> "RetentionConfig":
        if self.strategy == "count":
            if not self.keep_latest or self.keep_latest < 1:
                raise ValueError("retention.keep_latest must be >= 1 when strategy is 'count'")
        elif self.strategy == "age":
            if not self.max_age_days or self.max_age_days < 1:
                raise ValueError("retention.max_age_days must be >= 1 when strategy is 'age'")
        elif self.strategy == "size":
            if not self.max_total_mb or self.max_total_mb < 1:
                raise ValueError("retention.max_total_mb must be >= 1 when strategy is 'size'")
        return self


class ChannelConfig(BaseModel):
    id: str
    name: str
    slug: str
    # No app-level default: every channel must be given a category when it's
    # created (bootstrap config.yaml entry or the admin "Add channel" form).
    # The database column has its own DEFAULT for pre-existing rows — see
    # db.py's CHANNEL_COLUMNS.
    category: Literal[*ITUNES_CATEGORIES]
    retention: RetentionConfig | None = None
    # A "channel" may instead be a single playlist, for when only part of a
    # YouTube channel is wanted; `id` then holds the playlist ID.
    source_type: Literal["channel", "playlist"] = "channel"

    @property
    def is_playlist(self) -> bool:
        return self.source_type == "playlist"


class AdminConfig(BaseModel):
    # Unset means the admin UI is disabled entirely (routes return 404) rather
    # than silently exposed with no password on a LAN device.
    password: str | None = None


class AppConfig(BaseModel):
    poll_interval_minutes: int = 20
    base_url: str
    data_dir: Path = Path("/data")
    channels: list[ChannelConfig] = Field(default_factory=list)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)

    @model_validator(mode="after")
    def check_unique_channels(self) -> "AppConfig":
        ids = [c.id for c in self.channels]
        slugs = [c.slug for c in self.channels]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate channel id in config")
        if len(slugs) != len(set(slugs)):
            raise ValueError("Duplicate channel slug in config")
        return self


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config file not found at {config_path}. "
            "Copy config/config.example.yaml there and edit it."
        )
    with config_path.open() as f:
        raw = yaml.safe_load(f) or {}
    return AppConfig(**raw)
