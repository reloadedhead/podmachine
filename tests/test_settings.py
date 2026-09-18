import pytest

from podmachine.config import RetentionConfig
from podmachine.db import connect, init_db
from podmachine.settings import (
    get_default_retention,
    import_default_retention_from_config_if_unset,
    set_default_retention,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    c = connect(db_path)
    yield c
    c.close()


def test_get_default_retention_defaults_to_none_strategy(conn):
    retention = get_default_retention(conn)
    assert retention.strategy == "none"


def test_set_and_get_default_retention(conn):
    set_default_retention(conn, RetentionConfig(strategy="count", keep_latest=5))
    retention = get_default_retention(conn)
    assert retention.strategy == "count"
    assert retention.keep_latest == 5


def test_set_default_retention_overwrites_previous_value(conn):
    set_default_retention(conn, RetentionConfig(strategy="count", keep_latest=5))
    set_default_retention(conn, RetentionConfig(strategy="none"))
    retention = get_default_retention(conn)
    assert retention.strategy == "none"


def test_import_default_retention_from_config_only_when_unset(conn):
    import_default_retention_from_config_if_unset(conn, RetentionConfig(strategy="count", keep_latest=10))
    assert get_default_retention(conn).keep_latest == 10

    # Setting is no longer unset: a second import with a different value is a no-op.
    import_default_retention_from_config_if_unset(conn, RetentionConfig(strategy="count", keep_latest=99))
    assert get_default_retention(conn).keep_latest == 10
