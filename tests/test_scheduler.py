from podmachine.config import AppConfig, ChannelConfig
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
        config, db_path, fetch_fn=lambda channel_id: catalog, download_fn=tracking_download, tag_fn=lambda *a, **kw: None
    )

    assert result["poll_results"][0]["baseline_established_now"] is True
    assert result["process_results"] == []
    assert download_calls == []


def test_run_cycle_downloads_newly_discovered_video_in_same_cycle(tmp_path):
    config = make_config(tmp_path, [CHANNEL])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    baseline_catalog = [make_entry("old1")]
    run_cycle(config, db_path, fetch_fn=lambda channel_id: baseline_catalog, tag_fn=lambda *a, **kw: None)

    updated_catalog = baseline_catalog + [make_entry("new1")]
    result = run_cycle(
        config, db_path, fetch_fn=lambda channel_id: updated_catalog, download_fn=fake_download, tag_fn=lambda *a, **kw: None
    )

    assert result["poll_results"][0]["new_pending"] == 1
    assert len(result["process_results"]) == 1
    assert result["process_results"][0]["video_id"] == "new1"
    assert result["process_results"][0]["success"] is True

    conn = connect(db_path)
    row = conn.execute("SELECT status FROM videos WHERE video_id = 'new1'").fetchone()
    assert row["status"] == "done"


def test_run_cycle_with_no_channels_returns_empty_results(tmp_path):
    config = make_config(tmp_path, [])
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)

    def unexpected_fetch(channel_id):
        raise AssertionError("fetch should not be called with zero channels")

    result = run_cycle(config, db_path, fetch_fn=unexpected_fetch)

    assert result == {"poll_results": [], "process_results": []}


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
