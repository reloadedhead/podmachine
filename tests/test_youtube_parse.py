from podmachine.youtube import parse_feed

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <id>yt:channel:testchannel0000000000</id>
 <yt:channelId>testchannel0000000000</yt:channelId>
 <title>Test Channel</title>
 <entry>
  <id>yt:video:aaaaaaaaaaa</id>
  <yt:videoId>aaaaaaaaaaa</yt:videoId>
  <yt:channelId>testchannel0000000000</yt:channelId>
  <title>Test Video One</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=aaaaaaaaaaa"/>
  <published>2026-08-10T12:00:00+00:00</published>
  <updated>2026-08-10T12:05:00+00:00</updated>
 </entry>
 <entry>
  <id>yt:video:bbbbbbbbbbb</id>
  <yt:videoId>bbbbbbbbbbb</yt:videoId>
  <yt:channelId>testchannel0000000000</yt:channelId>
  <title>Test Short One</title>
  <link rel="alternate" href="https://www.youtube.com/shorts/bbbbbbbbbbb"/>
  <published>2026-08-11T09:00:00+00:00</published>
  <updated>2026-08-11T09:05:00+00:00</updated>
 </entry>
</feed>
"""


def test_parse_feed_extracts_all_entries():
    entries = parse_feed(FEED_XML)
    assert [e.video_id for e in entries] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_parse_feed_detects_short_by_link_pattern():
    entries = parse_feed(FEED_XML)
    by_id = {e.video_id: e for e in entries}
    assert by_id["aaaaaaaaaaa"].is_short is False
    assert by_id["bbbbbbbbbbb"].is_short is True


def test_parse_feed_extracts_title_and_published():
    entries = parse_feed(FEED_XML)
    first = entries[0]
    assert first.title == "Test Video One"
    assert first.published_at == "2026-08-10T12:00:00+00:00"


def test_parse_feed_handles_empty_feed():
    empty = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <title>Empty Channel</title>
</feed>
"""
    assert parse_feed(empty) == []
