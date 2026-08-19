from datetime import datetime, timedelta, timezone
from pathlib import Path

from podmachine.config import AppConfig, ChannelConfig, RetentionConfig
from podmachine.db import connect, init_db
from podmachine.downloader import DownloadResult
from podmachine.scheduler import run_cycle, start_scheduler
from podmachine.youtube import VideoEntry

CHANNEL = ChannelConfig(id="UCtest0000000000000000", name="Test Channel", slug="test-channel")


def make_entry(video_id: str, published_at: str = "2026-08-10T12:00:00+00:00") -> VideoEntry:
    return VideoEntry(
        video_id=video_id,
        title=f"Video {video_id}",
        published_at=published_at,
        url=f"https://www.youtube.com/watch?v={video_id}",
        is_short=False,
    )


def make_config(tmp_path, channels):
    return AppConfig(base_url="http://podmachine.local:8000", data_dir=tmp_path, channels=channels)


def fake_download(video_id, channel_slug, media_dir):
    out_dir = media_dir / channel_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{video_id}.mp3"
    f.write_bytes(b"fake-audio")
    return DownloadResult(video_id=video_id, success=True, file_path=f, file_size=f.stat().st_size, info={})


def no_avatar(channel_id):
    return None


def test_run_cycle_first_poll_establishes_baseline_without_downloading(tmp_path):
    config = make_config(tmp_path, [CHANNEL])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    download_calls = []

    def tracking_download(video_id, channel_slug, media_dir):
        download_calls.append(video_id)
        return fake_download(video_id, channel_slug, media_dir)

    catalog = [make_entry("old1"), make_entry("old2")]
    result = run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: catalog,
        download_fn=tracking_download,
        tag_fn=lambda *a, **kw: None,
        avatar_url_fn=no_avatar,
    )

    assert result["poll_results"][0]["baseline_established_now"] is True
    assert result["process_results"] == []
    assert download_calls == []


def test_run_cycle_downloads_newly_discovered_video_in_same_cycle(tmp_path):
    config = make_config(tmp_path, [CHANNEL])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    baseline_catalog = [make_entry("old1")]
    run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: baseline_catalog,
        tag_fn=lambda *a, **kw: None,
        avatar_url_fn=no_avatar,
    )

    updated_catalog = baseline_catalog + [make_entry("new1")]
    result = run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: updated_catalog,
        download_fn=fake_download,
        tag_fn=lambda *a, **kw: None,
        avatar_url_fn=no_avatar,
    )

    assert result["poll_results"][0]["new_pending"] == 1
    assert len(result["process_results"]) == 1
    assert result["process_results"][0]["video_id"] == "new1"
    assert result["process_results"][0]["success"] is True

    conn = connect(db_path)
    row = conn.execute("SELECT status FROM videos WHERE video_id = 'new1'").fetchone()
    assert row["status"] == "done"


def test_run_cycle_fetches_and_caches_channel_avatar(tmp_path):
    config = make_config(tmp_path, [CHANNEL])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: [],
        avatar_url_fn=lambda channel_id: "https://example.com/avatar.jpg",
        avatar_bytes_fn=lambda url: b"fake-avatar-bytes",
    )

    conn = connect(db_path)
    row = conn.execute(
        "SELECT avatar_path FROM channel_state WHERE slug = ?", (CHANNEL.slug,)
    ).fetchone()
    assert row["avatar_path"] is not None
    assert Path(row["avatar_path"]).read_bytes() == b"fake-avatar-bytes"


def test_run_cycle_applies_configured_retention_policy(tmp_path):
    config = AppConfig(
        base_url="http://podmachine.local:8000",
        data_dir=tmp_path,
        retention=RetentionConfig(strategy="count", keep_latest=1),
        channels=[CHANNEL],
    )
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    baseline_catalog = [make_entry("old1", "2026-08-01T00:00:00+00:00")]
    run_cycle(config, db_path, fetch_fn=lambda channel_id: baseline_catalog, avatar_url_fn=no_avatar)

    updated_catalog = baseline_catalog + [make_entry("new1", "2026-08-10T00:00:00+00:00")]
    result = run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: updated_catalog,
        download_fn=fake_download,
        tag_fn=lambda *a, **kw: None,
        avatar_url_fn=no_avatar,
    )

    # old1 was baseline (never downloaded), new1 just got downloaded.
    # Retention (keep_latest=1) should have nothing to delete yet since
    # only one 'done' episode exists.
    assert result["retention_deleted"] == 0

    # Now a second new episode arrives; with keep_latest=1 the older of
    # the two 'done' episodes should be deleted this cycle.
    second_catalog = updated_catalog + [make_entry("new2", "2026-08-11T00:00:00+00:00")]
    result2 = run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: second_catalog,
        download_fn=fake_download,
        tag_fn=lambda *a, **kw: None,
        avatar_url_fn=no_avatar,
    )

    assert result2["retention_deleted"] == 1
    conn = connect(db_path)
    assert conn.execute("SELECT status FROM videos WHERE video_id = 'new1'").fetchone()["status"] == "deleted"
    assert conn.execute("SELECT status FROM videos WHERE video_id = 'new2'").fetchone()["status"] == "done"


def test_run_cycle_requeues_and_reprocesses_stale_failure_in_same_cycle(tmp_path):
    config = make_config(tmp_path, [CHANNEL])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    # Establish baseline with no channel content, independent of the
    # failed video we're about to seed by hand.
    run_cycle(config, db_path, fetch_fn=lambda channel_id: [], avatar_url_fn=no_avatar)

    conn = connect(db_path)
    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at, "
        "last_attempt_at, error_message) VALUES ('stuck1', ?, 'Title', '2026-08-01T00:00:00+00:00', "
        "'failed', '2026-08-01T00:00:00+00:00', ?, 'HTTP Error 403: Forbidden')",
        (CHANNEL.slug, stale),
    )
    conn.commit()
    conn.close()

    result = run_cycle(
        config,
        db_path,
        fetch_fn=lambda channel_id: [],
        download_fn=fake_download,
        tag_fn=lambda *a, **kw: None,
        avatar_url_fn=no_avatar,
    )

    assert result["requeued"] == 1
    assert len(result["process_results"]) == 1
    assert result["process_results"][0]["video_id"] == "stuck1"
    assert result["process_results"][0]["success"] is True

    conn = connect(db_path)
    row = conn.execute(
        "SELECT status, long_range_retry_count FROM videos WHERE video_id = 'stuck1'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["long_range_retry_count"] == 1


def test_run_cycle_with_no_channels_returns_empty_results(tmp_path):
    config = make_config(tmp_path, [])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    def unexpected_fetch(channel_id):
        raise AssertionError("fetch should not be called with zero channels")

    result = run_cycle(config, db_path, fetch_fn=unexpected_fetch)

    assert result == {"poll_results": [], "process_results": [], "retention_deleted": 0, "requeued": 0}


def test_start_scheduler_registers_job_with_configured_interval(tmp_path):
    config = make_config(tmp_path, [])  # no channels: the immediate first run is a no-op, safe to let it fire for real
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    config = config.model_copy(update={"poll_interval_minutes": 5})

    scheduler = start_scheduler(config, db_path)
    try:
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "podmachine-cycle"
        assert jobs[0].trigger.interval.total_seconds() == 5 * 60
    finally:
        scheduler.shutdown(wait=False)
