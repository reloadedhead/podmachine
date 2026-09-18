from __future__ import annotations

from unittest.mock import MagicMock

import podmachine.config
import podmachine.main
import pytest
from fastapi.testclient import TestClient

from podmachine.db import connect
from podmachine.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"base_url: http://podmachine.local:8000\n"
        f"data_dir: {tmp_path}\n"
        "admin:\n"
        "  password: secret123\n"
        "channels:\n"
        "  - id: UC1\n"
        "    name: Example Channel\n"
        "    slug: example-channel\n"
    )
    # load_config() reads the DEFAULT_CONFIG_PATH module constant (baked in
    # at import time from the env var), so patch that directly rather than
    # relying on env var timing.
    monkeypatch.setattr(podmachine.config, "DEFAULT_CONFIG_PATH", config_path)
    # The real scheduler fires an immediate background poll/download cycle
    # against YouTube on startup; these tests exercise the HTTP layer only.
    monkeypatch.setattr(podmachine.main, "start_scheduler", lambda config, db_path: MagicMock())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_admin_client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"base_url: http://podmachine.local:8000\ndata_dir: {tmp_path}\nchannels: []\n")
    monkeypatch.setattr(podmachine.config, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr(podmachine.main, "start_scheduler", lambda config, db_path: MagicMock())
    with TestClient(app) as c:
        yield c


def test_admin_dashboard_requires_auth(client):
    response = client.get("/admin/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_admin_dashboard_rejects_wrong_password(client):
    response = client.get("/admin/", auth=("admin", "wrong"))
    assert response.status_code == 401


def test_admin_dashboard_renders_with_correct_password(client):
    response = client.get("/admin/", auth=("admin", "secret123"))
    assert response.status_code == 200
    assert "Example Channel" in response.text
    assert "htmx.min.js" in response.text


def test_admin_disabled_without_configured_password(no_admin_client):
    response = no_admin_client.get("/admin/")
    assert response.status_code == 404


def test_channel_detail_unknown_slug_404s(client):
    response = client.get("/admin/channels/does-not-exist", auth=("admin", "secret123"))
    assert response.status_code == 404


def test_channel_detail_renders(client):
    response = client.get("/admin/channels/example-channel", auth=("admin", "secret123"))
    assert response.status_code == 200
    assert "Example Channel" in response.text
    assert "No episodes yet" in response.text


def test_action_poll_triggers_poll_and_returns_updated_fragment(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "podmachine.web.routes.poll_all_channels",
        lambda conn, channels: calls.append(channels) or [],
    )
    response = client.post("/admin/actions/poll", auth=("admin", "secret123"))
    assert response.status_code == 200
    assert len(calls) == 1
    assert 'id="channel-table"' in response.text


def test_static_htmx_served_without_auth(client):
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200


def test_json_channels_endpoint_still_unauthenticated(client):
    response = client.get("/channels")
    assert response.status_code == 200
    assert response.json()["channels"][0]["slug"] == "example-channel"


def _insert_video(client, video_id, status, file_path=None, error_message=None):
    conn = connect(client.app.state.db_path)
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at, "
        "file_path, error_message) VALUES (?, 'example-channel', 'T', '2024-01-01T00:00:00+00:00', "
        "?, '2024-01-01T00:00:00+00:00', ?, ?)",
        (video_id, status, file_path, error_message),
    )
    conn.commit()
    conn.close()


def test_action_retry_requeues_failed_video(client):
    _insert_video(client, "vid1", "failed", error_message="boom")
    response = client.post(
        "/admin/channels/example-channel/videos/vid1/retry", auth=("admin", "secret123")
    )
    assert response.status_code == 200
    assert 'id="video-table"' in response.text

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT status, error_message FROM videos WHERE video_id = ?", ("vid1",)).fetchone()
    conn.close()
    assert row["status"] == "pending"
    assert row["error_message"] is None


def test_action_delete_tombstones_video_and_removes_file(client, tmp_path):
    media_file = tmp_path / "vid2.mp3"
    media_file.write_bytes(b"audio")
    _insert_video(client, "vid2", "done", file_path=str(media_file))

    response = client.post(
        "/admin/channels/example-channel/videos/vid2/delete", auth=("admin", "secret123")
    )
    assert response.status_code == 200
    assert 'id="video-table"' in response.text
    assert not media_file.exists()

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT status, file_path FROM videos WHERE video_id = ?", ("vid2",)).fetchone()
    conn.close()
    assert row["status"] == "deleted"
    assert row["file_path"] is None


def test_action_retry_unknown_video_404s(client):
    response = client.post(
        "/admin/channels/example-channel/videos/does-not-exist/retry", auth=("admin", "secret123")
    )
    assert response.status_code == 404


def test_action_queue_baseline_video(client):
    _insert_video(client, "vid3", "baseline")
    response = client.post(
        "/admin/channels/example-channel/videos/vid3/queue", auth=("admin", "secret123")
    )
    assert response.status_code == 200
    assert 'id="video-table"' in response.text

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT status FROM videos WHERE video_id = ?", ("vid3",)).fetchone()
    conn.close()
    assert row["status"] == "pending"


def test_action_queue_skipped_short_video(client):
    _insert_video(client, "vid4", "skipped_short")
    response = client.post(
        "/admin/channels/example-channel/videos/vid4/queue", auth=("admin", "secret123")
    )
    assert response.status_code == 200

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT status FROM videos WHERE video_id = ?", ("vid4",)).fetchone()
    conn.close()
    assert row["status"] == "pending"


def test_action_queue_deleted_video(client):
    _insert_video(client, "vid5", "deleted")
    response = client.post(
        "/admin/channels/example-channel/videos/vid5/queue", auth=("admin", "secret123")
    )
    assert response.status_code == 200

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT status FROM videos WHERE video_id = ?", ("vid5",)).fetchone()
    conn.close()
    assert row["status"] == "pending"


def test_action_queue_rejects_already_pending_video(client):
    _insert_video(client, "vid6", "pending")
    response = client.post(
        "/admin/channels/example-channel/videos/vid6/queue", auth=("admin", "secret123")
    )
    assert response.status_code == 400


def test_action_queue_rejects_done_video(client):
    _insert_video(client, "vid7", "done")
    response = client.post(
        "/admin/channels/example-channel/videos/vid7/queue", auth=("admin", "secret123")
    )
    assert response.status_code == 400


def test_action_queue_unknown_video_404s(client):
    response = client.post(
        "/admin/channels/example-channel/videos/does-not-exist/queue", auth=("admin", "secret123")
    )
    assert response.status_code == 404


def test_action_add_channel(client):
    response = client.post(
        "/admin/channels",
        data={"channel_id": "UC2", "name": "New Channel", "slug": "new-channel"},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 200
    assert "New Channel" in response.text

    detail = client.get("/admin/channels/new-channel", auth=("admin", "secret123"))
    assert detail.status_code == 200


def test_action_add_channel_rejects_duplicate_slug(client):
    response = client.post(
        "/admin/channels",
        data={"channel_id": "UC9", "name": "Dup", "slug": "example-channel"},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 400
    assert "already exists" in response.text


def test_action_add_channel_autofetches_name_and_slug(client, monkeypatch):
    monkeypatch.setattr("podmachine.web.routes.fetch_channel_name", lambda channel_id: "Fetched Name")
    response = client.post(
        "/admin/channels", data={"channel_id": "UC2"}, auth=("admin", "secret123")
    )
    assert response.status_code == 200
    assert "Fetched Name" in response.text

    channel = client.get("/admin/channels/fetched-name", auth=("admin", "secret123"))
    assert channel.status_code == 200


def test_action_add_channel_dedupes_slug_collision(client, monkeypatch):
    monkeypatch.setattr("podmachine.web.routes.fetch_channel_name", lambda channel_id: "Example Channel")
    response = client.post(
        "/admin/channels", data={"channel_id": "UC2"}, auth=("admin", "secret123")
    )
    assert response.status_code == 200

    channel = client.get("/admin/channels/example-channel-2", auth=("admin", "secret123"))
    assert channel.status_code == 200
    assert "Example Channel" in channel.text


def test_action_add_channel_fetch_failure_shows_error(client, monkeypatch):
    import requests

    def raise_error(channel_id):
        raise requests.RequestException("network down")

    monkeypatch.setattr("podmachine.web.routes.fetch_channel_name", raise_error)
    response = client.post(
        "/admin/channels", data={"channel_id": "UC2"}, auth=("admin", "secret123")
    )
    assert response.status_code == 400
    assert "fetch a channel name" in response.text


def test_action_add_channel_manual_name_and_slug_skip_fetch(client, monkeypatch):
    def unexpected_fetch(channel_id):
        raise AssertionError("fetch_channel_name should not be called when name is provided")

    monkeypatch.setattr("podmachine.web.routes.fetch_channel_name", unexpected_fetch)
    response = client.post(
        "/admin/channels",
        data={"channel_id": "UC2", "name": "Manual Name", "slug": "manual-slug"},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 200
    assert "Manual Name" in response.text


def test_action_delete_channel_removes_it(client):
    response = client.post("/admin/channels/example-channel/delete", auth=("admin", "secret123"))
    assert response.status_code == 200
    assert "Example Channel" not in response.text

    detail = client.get("/admin/channels/example-channel", auth=("admin", "secret123"))
    assert detail.status_code == 404


def test_action_delete_channel_unknown_slug_404s(client):
    response = client.post("/admin/channels/does-not-exist/delete", auth=("admin", "secret123"))
    assert response.status_code == 404


def test_action_update_retention(client):
    response = client.post(
        "/admin/channels/example-channel/retention",
        data={"strategy": "count", "keep_latest": "3"},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 200
    assert 'id="channel-meta"' in response.text

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT retention_json FROM channels WHERE slug = 'example-channel'").fetchone()
    conn.close()
    assert '"strategy":"count"' in row["retention_json"]


def test_action_update_retention_clears_with_blank_strategy(client):
    client.post(
        "/admin/channels/example-channel/retention",
        data={"strategy": "count", "keep_latest": "3"},
        auth=("admin", "secret123"),
    )
    response = client.post(
        "/admin/channels/example-channel/retention",
        data={"strategy": "", "keep_latest": ""},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 200

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT retention_json FROM channels WHERE slug = 'example-channel'").fetchone()
    conn.close()
    assert row["retention_json"] is None


def test_settings_page_shows_default_retention(client):
    response = client.get("/admin/settings", auth=("admin", "secret123"))
    assert response.status_code == 200
    assert 'id="default-retention"' in response.text


def test_settings_page_requires_auth(client):
    response = client.get("/admin/settings")
    assert response.status_code == 401


def test_action_update_default_retention(client):
    response = client.post(
        "/admin/settings/retention",
        data={"strategy": "count", "keep_latest": "12"},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 200
    assert 'id="default-retention"' in response.text

    conn = connect(client.app.state.db_path)
    row = conn.execute("SELECT value FROM settings WHERE key = 'default_retention'").fetchone()
    conn.close()
    assert '"strategy":"count"' in row["value"]
    assert '"keep_latest":12' in row["value"]


def test_action_update_default_retention_rejects_count_without_keep_latest(client):
    response = client.post(
        "/admin/settings/retention",
        data={"strategy": "count", "keep_latest": ""},
        auth=("admin", "secret123"),
    )
    assert response.status_code == 400
