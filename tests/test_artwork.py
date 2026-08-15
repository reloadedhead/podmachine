from podmachine.artwork import ensure_channel_artwork
from podmachine.config import ChannelConfig
from podmachine.db import connect, init_db

CHANNEL = ChannelConfig(id="UCtest0000000000000000", name="Test Channel", slug="test-channel")


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def avatar_path_in_db(conn, slug):
    row = conn.execute("SELECT avatar_path FROM channel_state WHERE slug = ?", (slug,)).fetchone()
    return row["avatar_path"] if row else None


def test_fetches_and_caches_avatar_when_missing(tmp_path):
    conn = make_conn(tmp_path)
    artwork_dir = tmp_path / "artwork"

    ensure_channel_artwork(
        conn,
        CHANNEL,
        artwork_dir,
        avatar_url_fn=lambda channel_id: "https://example.com/avatar.jpg",
        fetch_bytes_fn=lambda url: b"fake-jpeg-bytes",
    )

    expected_path = artwork_dir / "test-channel.jpg"
    assert expected_path.read_bytes() == b"fake-jpeg-bytes"
    assert avatar_path_in_db(conn, CHANNEL.slug) == str(expected_path)


def test_skips_fetch_when_already_cached_and_file_exists(tmp_path):
    conn = make_conn(tmp_path)
    artwork_dir = tmp_path / "artwork"
    artwork_dir.mkdir(parents=True)
    cached = artwork_dir / "test-channel.jpg"
    cached.write_bytes(b"already-here")
    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established, avatar_path) VALUES (?, ?, 1, ?)",
        (CHANNEL.slug, CHANNEL.id, str(cached)),
    )
    conn.commit()

    calls = []
    ensure_channel_artwork(
        conn,
        CHANNEL,
        artwork_dir,
        avatar_url_fn=lambda channel_id: calls.append(channel_id) or "https://example.com/avatar.jpg",
        fetch_bytes_fn=lambda url: b"should-not-be-written",
    )

    assert calls == []
    assert cached.read_bytes() == b"already-here"


def test_refetches_when_cached_path_recorded_but_file_missing(tmp_path):
    conn = make_conn(tmp_path)
    artwork_dir = tmp_path / "artwork"
    missing_path = artwork_dir / "test-channel.jpg"  # never actually written
    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established, avatar_path) VALUES (?, ?, 1, ?)",
        (CHANNEL.slug, CHANNEL.id, str(missing_path)),
    )
    conn.commit()

    ensure_channel_artwork(
        conn,
        CHANNEL,
        artwork_dir,
        avatar_url_fn=lambda channel_id: "https://example.com/avatar.jpg",
        fetch_bytes_fn=lambda url: b"refetched-bytes",
    )

    assert missing_path.read_bytes() == b"refetched-bytes"


def test_no_avatar_found_leaves_state_unset_without_raising(tmp_path):
    conn = make_conn(tmp_path)
    artwork_dir = tmp_path / "artwork"

    ensure_channel_artwork(
        conn,
        CHANNEL,
        artwork_dir,
        avatar_url_fn=lambda channel_id: None,
        fetch_bytes_fn=lambda url: b"unused",
    )

    assert avatar_path_in_db(conn, CHANNEL.slug) is None
    assert not artwork_dir.exists()


def test_fetch_failure_is_caught_and_does_not_raise(tmp_path):
    conn = make_conn(tmp_path)
    artwork_dir = tmp_path / "artwork"

    def failing_fetch(channel_id):
        raise RuntimeError("network unreachable")

    ensure_channel_artwork(conn, CHANNEL, artwork_dir, avatar_url_fn=failing_fetch)

    assert avatar_path_in_db(conn, CHANNEL.slug) is None
