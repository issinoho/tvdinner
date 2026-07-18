import pytest

from tvdinner.player import _format_channels, _format_fps, _short_codec_name


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
