from __future__ import annotations

from unittest.mock import MagicMock

import podmachine.config
import podmachine.main
import pytest
from fastapi.testclient import TestClient

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
