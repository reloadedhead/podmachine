from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_CONFIG_PATH = Path(os.environ.get("PODMACHINE_CONFIG", "/config/config.yaml"))


class ChannelConfig(BaseModel):
    id: str
    name: str
    slug: str


class AppConfig(BaseModel):
    poll_interval_minutes: int = 20
    base_url: str
    data_dir: Path = Path("/data")
    channels: list[ChannelConfig] = Field(default_factory=list)

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
