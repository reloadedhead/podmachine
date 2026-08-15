from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path(os.environ.get("PODMACHINE_CONFIG", "/config/config.yaml"))


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
    retention: RetentionConfig | None = None


class AppConfig(BaseModel):
    poll_interval_minutes: int = 20
    base_url: str
    data_dir: Path = Path("/data")
    channels: list[ChannelConfig] = Field(default_factory=list)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)

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
