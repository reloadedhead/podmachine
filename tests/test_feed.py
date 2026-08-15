import xml.etree.ElementTree as ET

from podmachine.config import ChannelConfig
from podmachine.db import connect, init_db
from podmachine.feed import build_channel_feed

NAMESPACES = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
CHANNEL = ChannelConfig(id="UCxxx", name="Chan Name", slug="chan")
BASE_URL = "http://podmachine.local:8000"


def make_conn(tmp_path):
    db_path = tmp_path / "podmachine.sqlite3"
    init_db(db_path)
    return connect(db_path)


def seed_video(conn, video_id, status, published_at, **extra):
    fields = {
        "title": f"Title {video_id}",
        "description": None,
        "thumbnail_url": None,
        "duration_seconds": None,
        "file_size": None,
        **extra,
    }
    conn.execute(
        "INSERT INTO videos (video_id, channel_slug, title, published_at, status, discovered_at, "
        "description, thumbnail_url, duration_seconds, file_size) "
        "VALUES (?, 'chan', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            video_id,
            fields["title"],
            published_at,
            status,
            published_at,
            fields["description"],
            fields["thumbnail_url"],
            fields["duration_seconds"],
            fields["file_size"],
        ),
    )
    conn.commit()


def parse_items(xml_text):
    root = ET.fromstring(xml_text)
    return root.findall("./channel/item")


def test_only_done_episodes_appear_in_feed(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "done1", "done", "2026-08-10T12:00:00+00:00", file_size=1000)
    seed_video(conn, "pending1", "pending", "2026-08-11T12:00:00+00:00")
    seed_video(conn, "baseline1", "baseline", "2026-08-09T12:00:00+00:00")
    seed_video(conn, "short1", "skipped_short", "2026-08-12T12:00:00+00:00")

    xml = build_channel_feed(conn, CHANNEL, BASE_URL)
    items = parse_items(xml)

    assert len(items) == 1
    assert items[0].findtext("guid") == "done1"


def test_episodes_ordered_newest_first(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "older", "done", "2026-08-10T12:00:00+00:00", file_size=1000)
    seed_video(conn, "newer", "done", "2026-08-11T12:00:00+00:00", file_size=1000)

    xml = build_channel_feed(conn, CHANNEL, BASE_URL)
    items = parse_items(xml)

    assert [item.findtext("guid") for item in items] == ["newer", "older"]


def test_enclosure_and_guid_fields(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "vid1", "done", "2026-08-10T12:00:00+00:00", file_size=123456)

    xml = build_channel_feed(conn, CHANNEL, BASE_URL)
    item = parse_items(xml)[0]

    enclosure = item.find("enclosure")
    assert enclosure.get("url") == f"{BASE_URL}/media/chan/vid1.mp3"
    assert enclosure.get("length") == "123456"
    assert enclosure.get("type") == "audio/mpeg"

    guid = item.find("guid")
    assert guid.text == "vid1"
    assert guid.get("isPermaLink") == "false"


def test_itunes_duration_present_when_known(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "vid1", "done", "2026-08-10T12:00:00+00:00", file_size=1000, duration_seconds=245)
    seed_video(conn, "vid2", "done", "2026-08-11T12:00:00+00:00", file_size=1000)

    xml = build_channel_feed(conn, CHANNEL, BASE_URL)
    items = {item.findtext("guid"): item for item in parse_items(xml)}

    assert items["vid1"].findtext("itunes:duration", namespaces=NAMESPACES) == "245"
    assert items["vid2"].find("itunes:duration", NAMESPACES) is None


def test_description_falls_back_to_title_when_missing(tmp_path):
    conn = make_conn(tmp_path)
    seed_video(conn, "vid1", "done", "2026-08-10T12:00:00+00:00", file_size=1000, description=None)

    xml = build_channel_feed(conn, CHANNEL, BASE_URL)
    item = parse_items(xml)[0]

    assert item.findtext("description") == "Title vid1"


def test_empty_channel_produces_valid_feed_with_no_items(tmp_path):
    conn = make_conn(tmp_path)

    xml = build_channel_feed(conn, CHANNEL, BASE_URL)
    root = ET.fromstring(xml)

    assert root.findtext("./channel/title") == "Chan Name"
    assert parse_items(xml) == []
