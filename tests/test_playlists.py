import sqlite3
import xml.etree.ElementTree as ET

import pytest

from podmachine.artwork import ensure_channel_artwork
from podmachine.channels import add_channel, get_channel
from podmachine.config import ChannelConfig
from podmachine.db import connect, init_db
from podmachine.feed import build_channel_feed
from podmachine.poller import poll_channel
from podmachine.processor import process_pending_videos
from podmachine.downloader import DownloadResult
from podmachine.youtube import VideoEntry, parse_playlist_entries, parse_source

PLAYLIST = ChannelConfig(
    id="PLabc123", name="Some Playlist", slug="some-playlist", category="Comedy", source_type="playlist"
)


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def make_entry(video_id, is_short=False):
    return VideoEntry(
        video_id=video_id,
        title=f"Video {video_id}",
        published_at="2026-08-10T12:00:00+00:00",
        url=f"https://www.youtube.com/watch?v={video_id}",
        is_short=is_short,
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("UCabc", ("channel", "UCabc")),
        ("  UCabc  ", ("channel", "UCabc")),
        ("https://www.youtube.com/channel/UCabc-_1", ("channel", "UCabc-_1")),
        ("PLxyz", ("playlist", "PLxyz")),
        ("UUabc", ("playlist", "UUabc")),
        ("https://www.youtube.com/playlist?list=PLxyz", ("playlist", "PLxyz")),
        ("https://www.youtube.com/watch?v=abc&list=PLxyz&index=2", ("playlist", "PLxyz")),
        ("youtube.com/playlist?list=PLxyz", ("playlist", "PLxyz")),
        ("https://www.youtube.com/show/VLPLxyz?sbp=KgthR2hx", ("playlist", "PLxyz")),
        ("https://www.youtube.com/browse/VLPLxyz", ("playlist", "PLxyz")),
        ("VLPLxyz", ("playlist", "PLxyz")),
    ],
)
def test_parse_source(raw, expected):
    assert parse_source(raw) == expected


@pytest.mark.parametrize("raw", ["@somehandle", "https://www.youtube.com/@somehandle", "hello"])
def test_parse_source_rejects_unsupported(raw):
    with pytest.raises(ValueError):
        parse_source(raw)


def test_parse_playlist_entries_maps_and_skips_unavailable():
    info = {
        "entries": [
            {"id": "a", "title": "First", "url": "https://www.youtube.com/watch?v=a"},
            {"id": "b", "title": "[Private video]"},
            {"id": "c", "title": "Third", "timestamp": 1786363200},
            {"id": None, "title": "No id"},
        ]
    }
    entries = parse_playlist_entries(info, now="2026-10-06T00:00:00+00:00")
    assert [e.video_id for e in entries] == ["a", "c"]
    assert entries[0].published_at == "2026-10-06T00:00:00+00:00"
    assert entries[0].is_short is False
    assert entries[1].published_at == "2026-08-10T12:00:00+00:00"
    assert entries[1].url == "https://www.youtube.com/watch?v=c"


def test_poll_playlist_uses_playlist_fetcher_and_never_skips_shorts(tmp_path):
    conn = make_conn(tmp_path)

    def channel_fetch(_):
        raise AssertionError("channel RSS fetcher should not be used for playlists")

    catalog = [make_entry("old1")]
    result = poll_channel(conn, PLAYLIST, fetch=channel_fetch, fetch_playlist=lambda pid: catalog)
    assert result.baseline_established_now is True

    catalog = [make_entry("old1"), make_entry("new1"), make_entry("new2", is_short=True)]
    result = poll_channel(conn, PLAYLIST, fetch=channel_fetch, fetch_playlist=lambda pid: catalog)
    assert result.new_pending == 2
    statuses = dict(conn.execute("SELECT video_id, status FROM videos WHERE channel_slug = ?", (PLAYLIST.slug,)))
    assert statuses == {"old1": "baseline", "new1": "pending", "new2": "pending"}


def test_poll_playlist_passes_playlist_id(tmp_path):
    conn = make_conn(tmp_path)
    seen = []
    poll_channel(conn, PLAYLIST, fetch_playlist=lambda pid: seen.append(pid) or [])
    assert seen == ["PLabc123"]


def test_channel_source_type_round_trips(tmp_path):
    conn = make_conn(tmp_path)
    add_channel(conn, PLAYLIST)
    stored = get_channel(conn, PLAYLIST.slug)
    assert stored.source_type == "playlist"
    assert stored.is_playlist


def test_existing_channels_table_gets_channel_source_type(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    raw = sqlite3.connect(db_path)
    raw.execute("CREATE TABLE channels (slug TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, name TEXT NOT NULL, retention_json TEXT)")
    raw.execute("INSERT INTO channels (slug, id, name) VALUES ('old', 'UCold', 'Old')")
    raw.commit()
    raw.close()

    init_db(db_path)
    conn = connect(db_path)
    assert get_channel(conn, "old").source_type == "channel"


def test_playlist_feed_links_to_playlist(tmp_path):
    conn = make_conn(tmp_path)
    xml = build_channel_feed(conn, PLAYLIST, "http://podmachine.local")
    links = [el.text for el in ET.fromstring(xml).iter("link")]
    assert "https://www.youtube.com/playlist?list=PLabc123" in links


def test_playlist_artwork_uses_owner_avatar_fn(tmp_path):
    conn = make_conn(tmp_path)

    def channel_avatar(_):
        raise AssertionError("channel avatar lookup should not be used for playlists")

    ensure_channel_artwork(
        conn,
        PLAYLIST,
        tmp_path / "artwork",
        avatar_url_fn=channel_avatar,
        fetch_bytes_fn=lambda url: b"img",
        playlist_avatar_url_fn=lambda pid: f"https://example.com/{pid}.jpg",
    )
    assert (tmp_path / "artwork" / "some-playlist.jpg").read_bytes() == b"img"


def test_download_fills_real_publish_date(tmp_path):
    conn = make_conn(tmp_path)
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at) "
        "VALUES ('v1', 'some-playlist', 'T', '2026-10-06T00:00:00+00:00', 'pending', '2026-10-06T00:00:00+00:00')"
    )
    conn.commit()

    def fake_download(video_id, channel_slug, media_dir):
        f = media_dir / channel_slug / f"{video_id}.mp3"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"audio")
        return DownloadResult(video_id, True, f, 5, info={"timestamp": 1786363200})

    process_pending_videos(
        conn, tmp_path / "media", {}, download_fn=fake_download, tag_fn=lambda *a, **k: None, sleep_fn=lambda s: None
    )
    row = conn.execute("SELECT published_at FROM videos WHERE video_id = 'v1'").fetchone()
    assert row["published_at"] == "2026-08-10T12:00:00+00:00"
