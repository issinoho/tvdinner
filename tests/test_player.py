from datetime import datetime

import pytest

from tvdinner.player import _format_channels, _format_fps, _short_codec_name, list_recordings


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10", "H.264"),
        ("AAC (Advanced Audio Coding)", "AAC"),
        ("hevc", "hevc"),
        (None, None),
        ("", None),
    ],
)
def test_short_codec_name(raw, expected):
    assert _short_codec_name(raw) == expected


@pytest.mark.parametrize(
    "fps, expected",
    [
        (29.970029830932617, "29.97fps"),
        (30.0, "30fps"),
        (23.976023976023978, "23.98fps"),
        (0, None),
        (None, None),
    ],
)
def test_format_fps(fps, expected):
    assert _format_fps(fps) == expected


@pytest.mark.parametrize(
    "channels, expected",
    [
        ("stereo", "Stereo"),
        ("mono", "Mono"),
        ("5.1", "5.1"),
        ("7.1", "7.1"),
        (None, None),
        ("", None),
    ],
)
def test_format_channels(channels, expected):
    assert _format_channels(channels) == expected


def test_list_recordings_missing_directory_is_not_an_error(tmp_path):
    assert list_recordings(tmp_path / "does-not-exist") == []


def test_list_recordings_parses_label_and_timestamp(tmp_path):
    path = tmp_path / "BBC One_20260726-143005.ts"
    path.write_bytes(b"x" * 100)

    recordings = list_recordings(tmp_path)
    assert len(recordings) == 1
    assert recordings[0].label == "BBC One"
    assert recordings[0].path == path
    assert recordings[0].size_bytes == 100
    assert recordings[0].recorded_at == datetime(2026, 7, 26, 14, 30, 5)


def test_list_recordings_skips_files_not_matching_the_naming_pattern(tmp_path):
    (tmp_path / "not-a-recording.ts").write_bytes(b"x")
    (tmp_path / "label_not-a-timestamp.ts").write_bytes(b"x")
    (tmp_path / "readme.txt").write_bytes(b"x")

    assert list_recordings(tmp_path) == []


def test_list_recordings_sorts_newest_first(tmp_path):
    (tmp_path / "Older_20260101-100000.ts").write_bytes(b"x")
    (tmp_path / "Newer_20260726-100000.ts").write_bytes(b"x")

    recordings = list_recordings(tmp_path)
    assert [r.label for r in recordings] == ["Newer", "Older"]
