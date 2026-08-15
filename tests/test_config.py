import pytest
from pydantic import ValidationError

from podmachine.config import AppConfig, ChannelConfig, RetentionConfig


def test_retention_defaults_to_none_strategy():
    assert RetentionConfig().strategy == "none"


def test_count_strategy_requires_keep_latest():
    with pytest.raises(ValidationError):
        RetentionConfig(strategy="count")
    RetentionConfig(strategy="count", keep_latest=5)  # does not raise


def test_age_strategy_requires_max_age_days():
    with pytest.raises(ValidationError):
        RetentionConfig(strategy="age")
    RetentionConfig(strategy="age", max_age_days=30)  # does not raise


def test_size_strategy_requires_max_total_mb():
    with pytest.raises(ValidationError):
        RetentionConfig(strategy="size")
    RetentionConfig(strategy="size", max_total_mb=1000)  # does not raise


def test_app_config_retention_defaults_when_unset():
    config = AppConfig(base_url="http://x", channels=[])
    assert config.retention.strategy == "none"


def test_channel_retention_overrides_global_entirely(tmp_path):
    channel = ChannelConfig(
        id="UC1",
        name="A",
        slug="a",
        retention=RetentionConfig(strategy="count", keep_latest=3),
    )
    config = AppConfig(
        base_url="http://x",
        retention=RetentionConfig(strategy="count", keep_latest=100),
        channels=[channel],
    )
    effective = config.channels[0].retention or config.retention
    assert effective.keep_latest == 3  # channel override wins, not merged with global's 100


def test_channel_without_override_inherits_global():
    channel = ChannelConfig(id="UC1", name="A", slug="a")
    config = AppConfig(
        base_url="http://x",
        retention=RetentionConfig(strategy="count", keep_latest=15),
        channels=[channel],
    )
    effective = config.channels[0].retention or config.retention
    assert effective.keep_latest == 15
