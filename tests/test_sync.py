from podmachine.channels import import_channels_from_config_if_empty
from podmachine.config import AppConfig, ChannelConfig
from podmachine.db import connect, init_db
from podmachine.downloader import DownloadResult
from podmachine.sync import SyncManager
from podmachine.youtube import VideoEntry

CHANNEL = ChannelConfig(id="UCtest0000000000000000", name="Test Channel", slug="test-channel", category="Comedy")
OTHER = ChannelConfig(id="UCother000000000000000", name="Other Channel", slug="other-channel", category="News")


def no_sleep(seconds):
    pass


def make_entry(video_id: str) -> VideoEntry:
    return VideoEntry(
        video_id=video_id,
        title=f"Video {video_id}",
        published_at="2026-08-10T12:00:00+00:00",
        url=f"https://www.youtube.com/watch?v={video_id}",
        is_short=False,
    )


def make_env(tmp_path, channels=(CHANNEL,)):
    config = AppConfig(base_url="http://podmachine.local:8000", data_dir=tmp_path, channels=list(channels))
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    conn = connect(db_path)
    try:
        import_channels_from_config_if_empty(conn, config.channels)
        for channel in channels:
            # Baseline already established, so newly listed videos become pending.
            conn.execute(
                "INSERT INTO channel_state (slug, channel_id, baseline_established) VALUES (?, ?, 1)",
                (channel.slug, channel.id),
            )
        conn.commit()
    finally:
        conn.close()
    return config, db_path


def fake_download(video_id, channel_slug, media_dir):
    out_dir = media_dir / channel_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{video_id}.mp3"
    f.write_bytes(b"fake-audio")
    return DownloadResult(video_id=video_id, success=True, file_path=f, file_size=f.stat().st_size, info={})


def run_sync(manager, config, db_path, **fns):
    assert manager._begin() is True
    fns.setdefault("tag_fn", lambda *a, **kw: None)
    fns.setdefault("sleep_fn", no_sleep)
    manager._run(config, db_path, **fns)
    return manager.snapshot()


def test_idle_snapshot_before_any_sync():
    snap = SyncManager().snapshot()
    assert snap["phase"] == "idle"
    assert snap["running"] is False
    assert snap["percent"] == 0


def test_sync_polls_then_downloads_and_reports_progress_along_the_way(tmp_path):
    config, db_path = make_env(tmp_path)
    manager = SyncManager()
    seen = {}

    def fetch(channel_id):
        seen["polling"] = manager.snapshot()
        return [make_entry("new1")]

    def download(video_id, channel_slug, media_dir):
        seen["processing"] = manager.snapshot()
        return fake_download(video_id, channel_slug, media_dir)

    snap = run_sync(manager, config, db_path, fetch_fn=fetch, download_fn=download)

    assert seen["polling"]["phase"] == "polling"
    assert seen["polling"]["current"] == "Test Channel"
    assert seen["polling"]["channels"]["test-channel"]["state"] == "checking"
    assert seen["polling"]["running"] is True

    assert seen["processing"]["phase"] == "processing"
    assert seen["processing"]["current"] == "Video new1"
    assert seen["processing"]["channels"]["test-channel"]["state"] == "downloading"
    assert seen["processing"]["total"] == 1
    assert seen["processing"]["percent"] > 50

    assert snap["phase"] == "done"
    assert snap["downloaded"] == 1
    assert snap["has_errors"] is False
    assert snap["summary_title"] == "Sync complete"
    assert snap["summary_detail"] == "1 new episode downloaded from 1 channel."
    assert snap["channels"]["test-channel"]["state"] == "downloaded"
    assert snap["percent"] == 100

    conn = connect(db_path)
    row = conn.execute("SELECT status FROM videos WHERE video_id = 'new1'").fetchone()
    conn.close()
    assert row["status"] == "done"


def test_sync_with_nothing_new_skips_the_download_step(tmp_path):
    config, db_path = make_env(tmp_path)

    def download(*args):
        raise AssertionError("nothing is pending, so nothing should download")

    snap = run_sync(SyncManager(), config, db_path, fetch_fn=lambda cid: [], download_fn=download)

    assert snap["phase"] == "done"
    assert snap["summary_detail"] == "No new episodes."
    assert snap["channels"]["test-channel"]["state"] == "checked"


def test_sync_reports_a_channel_that_could_not_be_checked(tmp_path):
    config, db_path = make_env(tmp_path, channels=(CHANNEL, OTHER))

    def fetch(channel_id):
        if channel_id == CHANNEL.id:
            raise RuntimeError("HTTP 429")
        return [make_entry("new1")]

    snap = run_sync(SyncManager(), config, db_path, fetch_fn=fetch, download_fn=fake_download)

    assert snap["has_errors"] is True
    assert snap["poll_errors"] == [{"name": "Test Channel", "error": "HTTP 429"}]
    assert snap["channels"]["test-channel"]["state"] == "failed"
    assert snap["channels"]["other-channel"]["state"] == "downloaded"
    assert snap["summary_title"] == "Sync finished with 1 error"
    assert snap["summary_detail"] == "Test Channel couldn’t be checked. 1 new episode downloaded."


def test_sync_reports_a_failed_download(tmp_path):
    config, db_path = make_env(tmp_path)

    def failing_download(video_id, channel_slug, media_dir):
        return DownloadResult(video_id=video_id, success=False, error="403")

    snap = run_sync(
        SyncManager(), config, db_path, fetch_fn=lambda cid: [make_entry("new1")], download_fn=failing_download
    )

    assert snap["failed_downloads"] == 1
    assert snap["channels"]["test-channel"]["state"] == "failed"
    assert snap["summary_title"] == "Sync finished with 1 error"
    assert snap["summary_detail"] == "1 download failed."


def test_only_one_sync_runs_at_a_time(tmp_path):
    manager = SyncManager()
    assert manager._begin() is True
    assert manager.is_running() is True
    assert manager._begin() is False


def test_dismiss_clears_a_finished_summary_but_not_a_running_sync(tmp_path):
    config, db_path = make_env(tmp_path)
    manager = SyncManager()
    assert manager._begin() is True
    manager.dismiss()
    assert manager.snapshot()["phase"] == "polling"

    manager._run(config, db_path, fetch_fn=lambda cid: [], sleep_fn=no_sleep)
    assert manager.snapshot()["phase"] == "done"
    manager.dismiss()
    assert manager.snapshot()["phase"] == "idle"


def test_finished_summary_expires_on_its_own(tmp_path, monkeypatch):
    config, db_path = make_env(tmp_path)
    manager = SyncManager()
    run_sync(manager, config, db_path, fetch_fn=lambda cid: [])
    monkeypatch.setattr("podmachine.sync.SUMMARY_TTL_SECONDS", -1)
    assert manager.snapshot()["phase"] == "idle"
