from podmachine.db import connect, init_db
from podmachine.downloader import DownloadResult
from podmachine.processor import process_pending_videos


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def seed_video(conn, video_id="vid1", channel_slug="chan", status="pending"):
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at) "
        "VALUES (?, ?, 'Some Title', '2026-08-10T12:00:00+00:00', ?, '2026-08-10T12:00:00+00:00')",
        (video_id, channel_slug, status),
    )
    conn.commit()


def test_successful_download_marks_done_and_tags(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn)
    media_dir = tmp_path / "media"
    tagged_calls = []

    def fake_download(video_id, channel_slug, media_dir):
        out_dir = media_dir / channel_slug
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / f"{video_id}.mp3"
        f.write_bytes(b"fake-audio")
        return DownloadResult(
            video_id=video_id,
            success=True,
            file_path=f,
            file_size=f.stat().st_size,
            info={"description": "d", "thumbnail": "https://example.com/thumb.jpg", "duration": 245},
        )

    def fake_tag(file_path, **kwargs):
        # Real tagging changes the file size (embedded artwork, comments);
        # simulate that so the recorded file_size can be checked for staleness.
        tagged_calls.append((file_path, kwargs["title"]))
        file_path.write_bytes(b"fake-audio-plus-id3-tags")

    results = process_pending_videos(
        conn, media_dir, {"chan": "Chan Name"}, download_fn=fake_download, tag_fn=fake_tag
    )

    assert len(results) == 1
    assert results[0].success is True
    assert tagged_calls == [(media_dir / "chan" / "vid1.mp3", "Some Title")]

    row = conn.execute(
        "SELECT status, file_path, file_size, description, thumbnail_url, duration_seconds "
        "FROM videos WHERE video_id = 'vid1'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["file_path"].endswith("vid1.mp3")
    assert row["file_size"] == len(b"fake-audio-plus-id3-tags")
    assert row["description"] == "d"
    assert row["thumbnail_url"] == "https://example.com/thumb.jpg"
    assert row["duration_seconds"] == 245


def test_failed_download_marks_failed_with_error(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn)

    def fake_download(video_id, channel_slug, media_dir):
        return DownloadResult(video_id=video_id, success=False, error="boom")

    results = process_pending_videos(
        conn, tmp_path / "media", {"chan": "Chan Name"}, download_fn=fake_download, tag_fn=lambda *a, **kw: None
    )

    assert results[0].success is False
    assert results[0].error == "boom"

    row = conn.execute("SELECT status, error_message FROM videos WHERE video_id = 'vid1'").fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "boom"


def test_tagging_failure_does_not_discard_a_successful_download(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn)
    media_dir = tmp_path / "media"

    def fake_download(video_id, channel_slug, media_dir):
        out_dir = media_dir / channel_slug
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / f"{video_id}.mp3"
        f.write_bytes(b"fake-audio")
        return DownloadResult(video_id=video_id, success=True, file_path=f, file_size=f.stat().st_size, info={})

    def failing_tag(file_path, **kwargs):
        raise RuntimeError("tagging exploded")

    results = process_pending_videos(
        conn, media_dir, {"chan": "Chan Name"}, download_fn=fake_download, tag_fn=failing_tag
    )

    assert results[0].success is True
    row = conn.execute("SELECT status FROM videos WHERE video_id = 'vid1'").fetchone()
    assert row["status"] == "done"


def test_success_after_a_prior_failure_clears_the_old_error(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn)

    def failing_download(video_id, channel_slug, media_dir):
        return DownloadResult(video_id=video_id, success=False, error="boom")

    process_pending_videos(
        conn, tmp_path / "media", {"chan": "Chan"}, download_fn=failing_download, tag_fn=lambda *a, **kw: None
    )
    row = conn.execute("SELECT status, error_message FROM videos WHERE video_id = 'vid1'").fetchone()
    assert row["status"] == "failed"
    assert row["error_message"] == "boom"

    conn.execute("UPDATE videos SET status = 'pending' WHERE video_id = 'vid1'")
    conn.commit()

    def succeeding_download(video_id, channel_slug, media_dir):
        out_dir = media_dir / "chan"
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / f"{video_id}.mp3"
        f.write_bytes(b"fake-audio")
        return DownloadResult(video_id=video_id, success=True, file_path=f, file_size=f.stat().st_size, info={})

    process_pending_videos(
        conn, tmp_path / "media", {"chan": "Chan"}, download_fn=succeeding_download, tag_fn=lambda *a, **kw: None
    )

    row = conn.execute("SELECT status, error_message FROM videos WHERE video_id = 'vid1'").fetchone()
    assert row["status"] == "done"
    assert row["error_message"] is None


def test_only_pending_videos_are_processed(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, video_id="baseline1", status="baseline")
    seed_video(conn, video_id="short1", status="skipped_short")

    calls = []

    def fake_download(video_id, channel_slug, media_dir):
        calls.append(video_id)
        return DownloadResult(video_id=video_id, success=True, file_path=media_dir / "x.mp3", file_size=0, info={})

    process_pending_videos(
        conn, tmp_path / "media", {"chan": "Chan"}, download_fn=fake_download, tag_fn=lambda *a, **kw: None
    )

    assert calls == []
