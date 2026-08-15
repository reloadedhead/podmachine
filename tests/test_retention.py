import xml.etree.ElementTree as ET

from podmachine.config import ChannelConfig, RetentionConfig
from podmachine.db import connect, init_db
from podmachine.feed import build_channel_feed
from podmachine.poller import poll_channel
from podmachine.retention import apply_retention
from podmachine.youtube import VideoEntry

CHANNEL = ChannelConfig(id="UCtest0000000000000000", name="Test Channel", slug="test-channel")
NONE_RETENTION = RetentionConfig(strategy="none")


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def seed_done_video(conn, media_dir, video_id, published_at, write_file=True):
    file_path = media_dir / "test-channel" / f"{video_id}.mp3"
    if write_file:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"fake-audio")
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at, "
        "file_path, file_size) VALUES (?, 'test-channel', ?, ?, 'done', ?, ?, ?)",
        (video_id, f"Title {video_id}", published_at, published_at, str(file_path), 10),
    )
    conn.commit()
    return file_path


def video_status(conn, video_id):
    row = conn.execute(
        "SELECT status, file_path, file_size, deleted_at FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    return dict(row)


def test_none_strategy_deletes_nothing(tmp_path):
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    seed_done_video(conn, media_dir, "vid1", "2026-08-01T00:00:00+00:00")

    deleted = apply_retention(conn, CHANNEL, NONE_RETENTION, media_dir)

    assert deleted == 0
    assert video_status(conn, "vid1")["status"] == "done"


def test_count_strategy_keeps_newest_n_deletes_rest(tmp_path):
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    for i, day in enumerate(["01", "02", "03", "04", "05"]):
        seed_done_video(conn, media_dir, f"vid{i}", f"2026-08-{day}T00:00:00+00:00")

    deleted = apply_retention(conn, CHANNEL, RetentionConfig(strategy="count", keep_latest=2), media_dir)

    assert deleted == 3
    # newest two (vid4 = Aug 5, vid3 = Aug 4) survive; rest deleted
    assert video_status(conn, "vid4")["status"] == "done"
    assert video_status(conn, "vid3")["status"] == "done"
    for vid in ("vid0", "vid1", "vid2"):
        assert video_status(conn, vid)["status"] == "deleted"


def test_count_strategy_noop_when_fewer_episodes_than_keep_latest(tmp_path):
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    seed_done_video(conn, media_dir, "vid1", "2026-08-01T00:00:00+00:00")
    seed_done_video(conn, media_dir, "vid2", "2026-08-02T00:00:00+00:00")

    deleted = apply_retention(conn, CHANNEL, RetentionConfig(strategy="count", keep_latest=10), media_dir)

    assert deleted == 0
    assert video_status(conn, "vid1")["status"] == "done"
    assert video_status(conn, "vid2")["status"] == "done"


def test_deletion_removes_file_and_clears_path_and_size(tmp_path):
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    file_path = seed_done_video(conn, media_dir, "vid1", "2026-08-01T00:00:00+00:00")
    seed_done_video(conn, media_dir, "vid2", "2026-08-02T00:00:00+00:00")  # kept, forces vid1 to be the deletable one
    assert file_path.exists()

    apply_retention(conn, CHANNEL, RetentionConfig(strategy="count", keep_latest=1), media_dir)

    assert not file_path.exists()
    row = video_status(conn, "vid1")
    assert row["status"] == "deleted"
    assert row["file_path"] is None
    assert row["file_size"] is None
    assert row["deleted_at"] is not None


def test_deletion_handles_already_missing_file_without_raising(tmp_path):
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    seed_done_video(conn, media_dir, "vid1", "2026-08-01T00:00:00+00:00", write_file=False)
    seed_done_video(conn, media_dir, "vid2", "2026-08-02T00:00:00+00:00")

    deleted = apply_retention(conn, CHANNEL, RetentionConfig(strategy="count", keep_latest=1), media_dir)

    assert deleted == 1
    assert video_status(conn, "vid1")["status"] == "deleted"


def test_deleted_episode_excluded_from_feed(tmp_path):
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    seed_done_video(conn, media_dir, "vid1", "2026-08-01T00:00:00+00:00")
    seed_done_video(conn, media_dir, "vid2", "2026-08-02T00:00:00+00:00")

    apply_retention(conn, CHANNEL, RetentionConfig(strategy="count", keep_latest=1), media_dir)

    xml = build_channel_feed(conn, CHANNEL, "http://podmachine.local:8000")
    guids = [item.findtext("guid") for item in ET.fromstring(xml).findall("./channel/item")]

    assert guids == ["vid2"]
    assert "vid1" not in guids


def test_deleted_episode_not_rediscovered_by_poller(tmp_path):
    # This is the regression test that matters most: tombstoning instead
    # of removing the row is the whole point (see docs/retention.md). If
    # this ever fails, retention and the downloader will fight forever.
    conn = make_conn(tmp_path)
    media_dir = tmp_path / "media"
    seed_done_video(conn, media_dir, "vid1", "2026-08-01T00:00:00+00:00")
    seed_done_video(conn, media_dir, "vid2", "2026-08-02T00:00:00+00:00")
    # Establish baseline so poll_channel treats new arrivals as pending, not baseline.
    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established) VALUES (?, ?, 1)",
        (CHANNEL.slug, CHANNEL.id),
    )
    conn.commit()

    apply_retention(conn, CHANNEL, RetentionConfig(strategy="count", keep_latest=1), media_dir)
    assert video_status(conn, "vid1")["status"] == "deleted"

    catalog = [
        VideoEntry(video_id="vid1", title="Title vid1", published_at="2026-08-01T00:00:00+00:00",
                   url="https://www.youtube.com/watch?v=vid1", is_short=False),
        VideoEntry(video_id="vid2", title="Title vid2", published_at="2026-08-02T00:00:00+00:00",
                   url="https://www.youtube.com/watch?v=vid2", is_short=False),
    ]
    result = poll_channel(conn, CHANNEL, fetch=lambda channel_id: catalog)

    assert result.new_pending == 0
    assert video_status(conn, "vid1")["status"] == "deleted"
