from datetime import datetime, timedelta, timezone

from podmachine.config import ChannelConfig
from podmachine.db import connect, init_db
from podmachine.poller import CIRCUIT_BREAKER_THRESHOLD, poll_all_channels, poll_channel
from podmachine.youtube import VideoEntry

CHANNEL = ChannelConfig(id="UCtest0000000000000000", name="Test Channel", slug="test-channel")
CHANNEL_B = ChannelConfig(id="UCtest1111111111111111", name="Test Channel B", slug="test-channel-b")


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


def channel_state_row(conn, slug):
    return conn.execute(
        "SELECT consecutive_poll_failures, backed_off_until, last_poll_error "
        "FROM channel_state WHERE slug = ?",
        (slug,),
    ).fetchone()


def test_circuit_breaker_opens_after_threshold_consecutive_failures(tmp_path):
    conn = make_conn(tmp_path)

    def failing_fetch(channel_id):
        raise RuntimeError("boom")

    for _ in range(CIRCUIT_BREAKER_THRESHOLD - 1):
        poll_channel(conn, CHANNEL, fetch=failing_fetch)
    row = channel_state_row(conn, CHANNEL.slug)
    assert row["consecutive_poll_failures"] == CIRCUIT_BREAKER_THRESHOLD - 1
    assert row["backed_off_until"] is None  # not yet at threshold

    poll_channel(conn, CHANNEL, fetch=failing_fetch)
    row = channel_state_row(conn, CHANNEL.slug)
    assert row["consecutive_poll_failures"] == CIRCUIT_BREAKER_THRESHOLD
    assert row["backed_off_until"] is not None
    assert row["last_poll_error"] == "boom"


def test_circuit_breaker_skips_polling_while_backed_off(tmp_path):
    conn = make_conn(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established, backed_off_until) "
        "VALUES (?, ?, 1, ?)",
        (CHANNEL.slug, CHANNEL.id, future),
    )
    conn.commit()

    def unexpected_fetch(channel_id):
        raise AssertionError("fetch should not be called while backed off")

    result = poll_channel(conn, CHANNEL, fetch=unexpected_fetch)

    assert result.skipped_backoff is True
    assert result.error is None


def test_circuit_breaker_resets_after_a_successful_poll(tmp_path):
    conn = make_conn(tmp_path)

    def failing_fetch(channel_id):
        raise RuntimeError("boom")

    poll_channel(conn, CHANNEL, fetch=failing_fetch)
    poll_channel(conn, CHANNEL, fetch=failing_fetch)
    assert channel_state_row(conn, CHANNEL.slug)["consecutive_poll_failures"] == 2

    poll_channel(conn, CHANNEL, fetch=lambda channel_id: [])

    row = channel_state_row(conn, CHANNEL.slug)
    assert row["consecutive_poll_failures"] == 0
    assert row["backed_off_until"] is None
    assert row["last_poll_error"] is None


def test_backoff_expires_and_polling_resumes(tmp_path):
    conn = make_conn(tmp_path)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    conn.execute(
        "INSERT INTO channel_state (slug, channel_id, baseline_established, backed_off_until) "
        "VALUES (?, ?, 1, ?)",
        (CHANNEL.slug, CHANNEL.id, past),
    )
    conn.commit()

    calls = []

    def fetch(channel_id):
        calls.append(channel_id)
        return []

    result = poll_channel(conn, CHANNEL, fetch=fetch)

    assert result.skipped_backoff is False
    assert calls == [CHANNEL.id]


def test_poll_all_channels_jitters_between_channels_but_not_after_last(tmp_path):
    conn = make_conn(tmp_path)

    sleeps = []
    poll_all_channels(
        conn, [CHANNEL, CHANNEL_B], fetch=lambda channel_id: [], sleep_fn=sleeps.append
    )

    # 2 channels -> 1 gap between them, none after the last one
    assert len(sleeps) == 1
    assert 1.0 <= sleeps[0] <= 4.0
