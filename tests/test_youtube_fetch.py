import pytest
import requests

from podmachine import youtube
from podmachine.youtube import FEED_MAX_ATTEMPTS, fetch_channel_feed

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>Chan</title>
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <title>A video</title>
    <published>2026-08-10T12:00:00+00:00</published>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
  </entry>
</feed>"""


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def install_responses(monkeypatch, responses):
    """Each item is a FakeResponse to return or an exception to raise."""
    calls = []

    def fake_get(url, **kwargs):
        item = responses[len(calls)]
        calls.append(url)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    return calls


def test_transient_404_is_retried_then_succeeds(monkeypatch):
    calls = install_responses(monkeypatch, [FakeResponse(404), FakeResponse(200, FEED_XML)])
    sleeps = []

    entries = fetch_channel_feed("UCx", sleep_fn=sleeps.append)

    assert [e.video_id for e in entries] == ["abc123"]
    assert len(calls) == 2
    assert sleeps == [youtube.FEED_RETRY_BASE_SECONDS]


def test_persistent_404_raises_after_all_attempts_with_exponential_backoff(monkeypatch):
    calls = install_responses(monkeypatch, [FakeResponse(404)] * FEED_MAX_ATTEMPTS)
    sleeps = []

    with pytest.raises(requests.HTTPError):
        fetch_channel_feed("UCx", sleep_fn=sleeps.append)

    assert len(calls) == FEED_MAX_ATTEMPTS
    assert sleeps == [2, 4]


def test_5xx_and_connection_errors_are_retried(monkeypatch):
    calls = install_responses(
        monkeypatch,
        [FakeResponse(503), requests.ConnectionError("reset"), FakeResponse(200, FEED_XML)],
    )

    entries = fetch_channel_feed("UCx", sleep_fn=lambda s: None)

    assert len(entries) == 1
    assert len(calls) == 3


def test_non_transient_client_error_is_not_retried(monkeypatch):
    calls = install_responses(monkeypatch, [FakeResponse(403)])
    sleeps = []

    with pytest.raises(requests.HTTPError):
        fetch_channel_feed("UCx", sleep_fn=sleeps.append)

    assert len(calls) == 1
    assert sleeps == []
