from podmachine.config import ChannelConfig
from podmachine.db import connect, init_db
from podmachine.poller import poll_channel
from podmachine.youtube import VideoEntry

CHANNEL = ChannelConfig(id="UCtest0000000000000000", name="Test Channel", slug="test-channel")


def make_entry(video_id: str, is_short: bool = False, published_at: str = "2026-08-10T12:00:00+00:00") -> VideoEntry:
    url = f"https://www.youtube.com/{'shorts' if is_short else 'watch?v='}{video_id}"
    return VideoEntry(video_id=video_id, title=f"Video {video_id}", published_at=published_at, url=url, is_short=is_short)


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def video_rows(conn, channel_slug):
    return {
        row["video_id"]: row["status"]
        for row in conn.execute("SELECT video_id, status FROM videos WHERE channel_slug = ?", (channel_slug,))
    }


def test_first_poll_establishes_baseline_without_downloading(tmp_path):
    conn = make_conn(tmp_path)
    existing_catalog = [make_entry("old1"), make_entry("old2"), make_entry("old3", is_short=True)]

    result = poll_channel(conn, CHANNEL, fetch=lambda channel_id: existing_catalog)

    assert result.baseline_established_now is True
    assert result.new_pending == 0
    assert result.new_skipped_shorts == 0

    rows = video_rows(conn, CHANNEL.slug)
    assert rows == {"old1": "baseline", "old2": "baseline", "old3": "baseline"}


def test_second_poll_detects_new_video_as_pending(tmp_path):
    conn = make_conn(tmp_path)
    baseline_catalog = [make_entry("old1"), make_entry("old2")]
    poll_channel(conn, CHANNEL, fetch=lambda channel_id: baseline_catalog)

    updated_catalog = baseline_catalog + [make_entry("new1")]
    result = poll_channel(conn, CHANNEL, fetch=lambda channel_id: updated_catalog)

    assert result.baseline_established_now is False
    assert result.new_pending == 1
    assert result.new_skipped_shorts == 0

    rows = video_rows(conn, CHANNEL.slug)
    assert rows == {"old1": "baseline", "old2": "baseline", "new1": "pending"}


def test_new_short_is_recorded_but_not_queued_pending(tmp_path):
    conn = make_conn(tmp_path)
    baseline_catalog = [make_entry("old1")]
    poll_channel(conn, CHANNEL, fetch=lambda channel_id: baseline_catalog)

    updated_catalog = baseline_catalog + [make_entry("short1", is_short=True)]
    result = poll_channel(conn, CHANNEL, fetch=lambda channel_id: updated_catalog)

    assert result.new_pending == 0
    assert result.new_skipped_shorts == 1
    assert video_rows(conn, CHANNEL.slug)["short1"] == "skipped_short"


def test_unchanged_feed_produces_no_new_videos_on_repeated_polls(tmp_path):
    conn = make_conn(tmp_path)
    catalog = [make_entry("old1"), make_entry("old2")]
    poll_channel(conn, CHANNEL, fetch=lambda channel_id: catalog)

    result_1 = poll_channel(conn, CHANNEL, fetch=lambda channel_id: catalog)
    result_2 = poll_channel(conn, CHANNEL, fetch=lambda channel_id: catalog)

    assert result_1.new_pending == 0
    assert result_2.new_pending == 0
    assert video_rows(conn, CHANNEL.slug) == {"old1": "baseline", "old2": "baseline"}


def test_poll_failure_is_reported_without_raising(tmp_path):
    conn = make_conn(tmp_path)

    def failing_fetch(channel_id):
        raise RuntimeError("network unreachable")

    result = poll_channel(conn, CHANNEL, fetch=failing_fetch)

    assert result.error == "network unreachable"
    assert result.new_pending == 0
