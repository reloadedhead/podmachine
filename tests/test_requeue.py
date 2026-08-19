from datetime import datetime, timedelta, timezone

from podmachine.db import connect, init_db
from podmachine.requeue import LONG_RANGE_RETRY_INTERVAL_HOURS, MAX_LONG_RANGE_RETRIES, requeue_stale_failures


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def iso_hours_ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def seed_video(conn, video_id, status, last_attempt_at=None, long_range_retry_count=0):
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at, "
        "last_attempt_at, long_range_retry_count) VALUES (?, 'chan', 'Title', "
        "'2026-08-10T12:00:00+00:00', ?, '2026-08-10T12:00:00+00:00', ?, ?)",
        (video_id, status, last_attempt_at, long_range_retry_count),
    )
    conn.commit()


def video_row(conn, video_id):
    return dict(
        conn.execute(
            "SELECT status, long_range_retry_count FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
    )


def test_requeues_failure_older_than_interval(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "vid1", "failed", last_attempt_at=iso_hours_ago(LONG_RANGE_RETRY_INTERVAL_HOURS + 1))

    count = requeue_stale_failures(conn)

    assert count == 1
    row = video_row(conn, "vid1")
    assert row["status"] == "pending"
    assert row["long_range_retry_count"] == 1


def test_does_not_requeue_recent_failure(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "vid1", "failed", last_attempt_at=iso_hours_ago(1))

    count = requeue_stale_failures(conn)

    assert count == 0
    assert video_row(conn, "vid1")["status"] == "failed"


def test_requeues_legacy_failure_with_no_last_attempt_at(tmp_path):
    # Rows written before this feature existed have no last_attempt_at at
    # all (NULL) — treat as immediately eligible rather than never-eligible.
    conn = make_conn(tmp_path)
    seed_video(conn, "vid1", "failed", last_attempt_at=None)

    count = requeue_stale_failures(conn)

    assert count == 1
    assert video_row(conn, "vid1")["status"] == "pending"


def test_does_not_requeue_once_retries_exhausted(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(
        conn,
        "vid1",
        "failed",
        last_attempt_at=iso_hours_ago(LONG_RANGE_RETRY_INTERVAL_HOURS + 1),
        long_range_retry_count=MAX_LONG_RANGE_RETRIES,
    )

    count = requeue_stale_failures(conn)

    assert count == 0
    row = video_row(conn, "vid1")
    assert row["status"] == "failed"
    assert row["long_range_retry_count"] == MAX_LONG_RANGE_RETRIES


def test_requeues_up_to_but_not_beyond_the_cap(tmp_path):
    conn = make_conn(tmp_path)
    old = iso_hours_ago(LONG_RANGE_RETRY_INTERVAL_HOURS + 1)
    seed_video(conn, "vid1", "failed", last_attempt_at=old, long_range_retry_count=MAX_LONG_RANGE_RETRIES - 1)

    count = requeue_stale_failures(conn)
    assert count == 1
    assert video_row(conn, "vid1")["long_range_retry_count"] == MAX_LONG_RANGE_RETRIES

    # Simulate it failing again immediately (as if reprocessed and failed once more)
    conn.execute(
        "UPDATE videos SET status = 'failed', last_attempt_at = ? WHERE video_id = 'vid1'", (old,)
    )
    conn.commit()

    count2 = requeue_stale_failures(conn)
    assert count2 == 0  # now at the cap, no further requeue


def test_ignores_non_failed_statuses(tmp_path):
    conn = make_conn(tmp_path)
    old = iso_hours_ago(LONG_RANGE_RETRY_INTERVAL_HOURS + 1)
    for status in ("done", "pending", "baseline", "skipped_short", "deleted", "downloading"):
        seed_video(conn, f"vid-{status}", status, last_attempt_at=old)

    count = requeue_stale_failures(conn)

    assert count == 0
    for status in ("done", "pending", "baseline", "skipped_short", "deleted", "downloading"):
        assert video_row(conn, f"vid-{status}")["status"] == status
