import pytest

from podmachine.channels import (
    DuplicateChannelError,
    add_channel,
    delete_channel,
    get_channel,
    import_channels_from_config_if_empty,
    list_channels,
    slugify,
    unique_slug,
    update_channel_retention,
)
from podmachine.config import ChannelConfig, RetentionConfig
from podmachine.db import connect, init_db

CHANNEL = ChannelConfig(id="UC1", name="Example Channel", slug="example-channel")


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    c = connect(db_path)
    yield c
    c.close()


def test_add_and_get_channel(conn):
    add_channel(conn, CHANNEL)
    channel = get_channel(conn, "example-channel")
    assert channel.id == "UC1"
    assert channel.name == "Example Channel"
    assert channel.retention is None


def test_add_channel_persists_retention(conn):
    channel = ChannelConfig(id="UC1", name="Example", slug="example", retention=RetentionConfig(strategy="count", keep_latest=5))
    add_channel(conn, channel)
    fetched = get_channel(conn, "example")
    assert fetched.retention.strategy == "count"
    assert fetched.retention.keep_latest == 5


def test_add_channel_rejects_duplicate_slug(conn):
    add_channel(conn, CHANNEL)
    with pytest.raises(DuplicateChannelError):
        add_channel(conn, ChannelConfig(id="UC2", name="Other", slug="example-channel"))


def test_add_channel_rejects_duplicate_id(conn):
    add_channel(conn, CHANNEL)
    with pytest.raises(DuplicateChannelError):
        add_channel(conn, ChannelConfig(id="UC1", name="Other", slug="other-slug"))


def test_get_channel_unknown_slug_returns_none(conn):
    assert get_channel(conn, "does-not-exist") is None


def test_delete_channel_removes_it(conn):
    add_channel(conn, CHANNEL)
    delete_channel(conn, "example-channel")
    assert get_channel(conn, "example-channel") is None


def test_update_channel_retention(conn):
    add_channel(conn, CHANNEL)
    update_channel_retention(conn, "example-channel", RetentionConfig(strategy="none"))
    assert get_channel(conn, "example-channel").retention.strategy == "none"

    update_channel_retention(conn, "example-channel", None)
    assert get_channel(conn, "example-channel").retention is None


def test_list_channels_sorted_by_name(conn):
    add_channel(conn, ChannelConfig(id="UC2", name="Zeta", slug="zeta"))
    add_channel(conn, ChannelConfig(id="UC1", name="Alpha", slug="alpha"))
    names = [c.name for c in list_channels(conn)]
    assert names == ["Alpha", "Zeta"]


def test_import_channels_from_config_only_when_empty(conn):
    import_channels_from_config_if_empty(conn, [CHANNEL])
    assert [c.slug for c in list_channels(conn)] == ["example-channel"]

    # Table is no longer empty: a second import with different channels is a no-op.
    import_channels_from_config_if_empty(conn, [ChannelConfig(id="UC9", name="Ignored", slug="ignored")])
    assert [c.slug for c in list_channels(conn)] == ["example-channel"]


def test_slugify_lowercases_and_replaces_non_alnum():
    assert slugify("Google for Developers") == "google-for-developers"
    assert slugify("  Weird!! Name?? ") == "weird-name"


def test_slugify_empty_input_falls_back():
    assert slugify("???") == "channel"


def test_unique_slug_returns_base_when_free(conn):
    assert unique_slug(conn, "example") == "example"


def test_unique_slug_appends_suffix_on_collision(conn):
    add_channel(conn, CHANNEL)  # slug "example-channel"
    assert unique_slug(conn, "example-channel") == "example-channel-2"

    add_channel(conn, ChannelConfig(id="UC2", name="Other", slug="example-channel-2"))
    assert unique_slug(conn, "example-channel") == "example-channel-3"
