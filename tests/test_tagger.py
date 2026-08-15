from mutagen.id3 import ID3

from podmachine.tagger import tag_audio_file


def fake_fetch_thumbnail(url: str) -> bytes:
    return b"\xff\xd8\xff\xe0FAKEJPEGDATA"


def test_tag_audio_file_writes_expected_frames(tmp_path):
    audio_path = tmp_path / "test.mp3"
    audio_path.write_bytes(b"")

    tag_audio_file(
        audio_path,
        title="My Video",
        channel_name="My Channel",
        published_at="2026-08-10T12:00:00+00:00",
        description="A description",
        thumbnail_url="https://example.com/thumb.jpg",
        fetch_thumbnail_fn=fake_fetch_thumbnail,
    )

    tags = ID3(audio_path)
    assert tags["TIT2"].text == ["My Video"]
    assert tags["TPE1"].text == ["My Channel"]
    assert tags["TALB"].text == ["My Channel"]
    assert str(tags["TDRC"].text[0]) == "2026-08-10"
    assert tags["COMM:desc:eng"].text == ["A description"]
    assert tags["APIC:Cover"].data == b"\xff\xd8\xff\xe0FAKEJPEGDATA"


def test_tag_audio_file_without_thumbnail_or_description(tmp_path):
    audio_path = tmp_path / "test2.mp3"
    audio_path.write_bytes(b"")

    tag_audio_file(
        audio_path,
        title="No Art",
        channel_name="Chan",
        published_at="2026-01-01T00:00:00+00:00",
    )

    tags = ID3(audio_path)
    assert tags["TIT2"].text == ["No Art"]
    assert "APIC:Cover" not in tags
    assert "COMM:desc:eng" not in tags


def test_tag_audio_file_survives_thumbnail_fetch_failure(tmp_path):
    audio_path = tmp_path / "test3.mp3"
    audio_path.write_bytes(b"")

    def failing_fetch(url: str) -> bytes:
        raise RuntimeError("network down")

    tag_audio_file(
        audio_path,
        title="Still Tagged",
        channel_name="Chan",
        published_at="2026-01-01T00:00:00+00:00",
        thumbnail_url="https://example.com/thumb.jpg",
        fetch_thumbnail_fn=failing_fetch,
    )

    tags = ID3(audio_path)
    assert tags["TIT2"].text == ["Still Tagged"]
    assert "APIC:Cover" not in tags


def test_retagging_replaces_previous_frames(tmp_path):
    audio_path = tmp_path / "test4.mp3"
    audio_path.write_bytes(b"")

    tag_audio_file(audio_path, title="First", channel_name="Chan", published_at="2026-01-01T00:00:00+00:00")
    tag_audio_file(audio_path, title="Second", channel_name="Chan", published_at="2026-01-01T00:00:00+00:00")

    tags = ID3(audio_path)
    assert tags["TIT2"].text == ["Second"]
