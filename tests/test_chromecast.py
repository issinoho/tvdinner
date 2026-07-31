import pytest

from tvdinner.chromecast import CastDevice, guess_content_type


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://example.com/stream.m3u8", "application/x-mpegurl"),
        ("http://example.com/movie.mp4", "video/mp4"),
        ("http://example.com/movie.mp4?token=abc", "video/mp4"),
        ("http://192.168.0.218:32400/library/parts/1/2/file.mp4?X-Plex-Token=abc", "video/mp4"),
        ("http://example.com/show.mkv", "video/x-matroska"),
        ("http://example.com/clip.webm", "video/webm"),
        ("http://example.com/live/user/pass/12345.ts", "video/mp2t"),
        ("http://example.com:8080/channel/stream", "video/mp2t"),
    ],
)
def test_guess_content_type(url, expected):
    assert guess_content_type(url) == expected


def test_cast_device_is_a_plain_name_plus_opaque_cast_object():
    device = CastDevice(name="Living Room Hub", cast=object())
    assert device.name == "Living Room Hub"
