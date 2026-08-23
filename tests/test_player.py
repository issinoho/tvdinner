from datetime import datetime
from pathlib import Path

import pytest

from tvdinner.player import (
    _format_bitrate,
    _format_channels,
    _format_container,
    _format_fps,
    _hdr_label,
    _short_codec_name,
    capture_recording_thumbnail,
    list_recordings,
    live_buffer_mpv_options,
)


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


@pytest.mark.parametrize(
    "file_format, expected",
    [
        ("mov,mp4,m4a,3gp,3g2,mj2", "MP4"),  # confirmed live against a real MP4 file
        ("matroska,webm", "MKV"),
        ("mpegts", "MPEG-TS"),
        ("hls,applehttp", "HLS"),
        ("some_unmapped_format,other_alias", "SOME_UNMAPPED_FORMAT"),
        (None, None),
        ("", None),
    ],
)
def test_format_container(file_format, expected):
    assert _format_container(file_format) == expected


@pytest.mark.parametrize(
    "bits_per_second, expected",
    [
        (128_000, "128 kbps"),
        (69_297, "69 kbps"),
        (8_200_000, "8.2 Mbps"),
        (1_000_000, "1.0 Mbps"),
        (0, None),
        (None, None),
    ],
)
def test_format_bitrate(bits_per_second, expected):
    assert _format_bitrate(bits_per_second) == expected


def test_hdr_label_plain_static_hdr10():
    # gamma=pq with neither of the two more-specific signals below is
    # plain static HDR10 -- confirmed live against a real HDR10 file's
    # own video-params (no colormatrix=dolbyvision, no scene-max-r).
    assert _hdr_label({"gamma": "pq"}) == "HDR10"


def test_hdr_label_dolby_vision():
    # mpv reports colormatrix as the literal string "dolbyvision" instead
    # of a normal YCbCr matrix name whenever DV metadata is present --
    # confirmed live against a real DV profile 8.1 stream.
    assert _hdr_label({"gamma": "pq", "colormatrix": "dolbyvision"}) == "Dolby Vision"


def test_hdr_label_hdr10_plus():
    # video-params/scene-max-r is only ever populated from real SMPTE
    # ST2094-40 (HDR10+) dynamic metadata, per mpv's own manual.
    assert _hdr_label({"gamma": "pq", "colormatrix": "bt.2020nc", "scene-max-r": 812.3}) == "HDR10+"


def test_hdr_label_max_pq_y_alone_is_not_hdr10_plus():
    # max-pq-y/avg-pq-y are mpv's own per-frame peak-detection stats,
    # populated for *any* PQ content regardless of whether the source
    # actually carries dynamic metadata -- they must never be mistaken
    # for the real HDR10+ dynamic-metadata signal (scene-max-r).
    assert _hdr_label({"gamma": "pq", "max-pq-y": 0.5, "avg-pq-y": 0.3}) == "HDR10"


def test_hdr_label_hlg():
    assert _hdr_label({"gamma": "hlg"}) == "HLG"


def test_hdr_label_sdr_is_none():
    assert _hdr_label({"gamma": "bt.1886"}) is None
    assert _hdr_label({}) is None


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


def test_live_buffer_mpv_options_scales_with_minutes():
    small = live_buffer_mpv_options(5)
    large = live_buffer_mpv_options(10)
    assert large["demuxer_max_back_bytes"] > small["demuxer_max_back_bytes"]
    assert large["demuxer_max_bytes"] > small["demuxer_max_bytes"]


def test_live_buffer_mpv_options_forward_cache_exceeds_back_cache():
    # The forward cache must be at least as large as the back-buffer it
    # contains, or mpv would be asked for an impossible configuration.
    options = live_buffer_mpv_options(10)
    assert options["demuxer_max_bytes"] > options["demuxer_max_back_bytes"]


class _FakeMPVBase:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.played = None
        self.terminated = False

    def play(self, path):
        self.played = path

    def terminate(self):
        self.terminated = True


def test_capture_recording_thumbnail_returns_produced_jpeg_bytes(tmp_path, monkeypatch):
    class _FakeMPV(_FakeMPVBase):
        def wait_for_playback(self, timeout=None):
            outdir = Path(self.kwargs["vo_image_outdir"])
            (outdir / "00000001.jpg").write_bytes(b"fake-jpeg-bytes")

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    assert capture_recording_thumbnail(tmp_path / "recording.ts") == b"fake-jpeg-bytes"


def test_capture_recording_thumbnail_plays_the_given_path_and_always_terminates(tmp_path, monkeypatch):
    instances = []

    class _FakeMPV(_FakeMPVBase):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

        def wait_for_playback(self, timeout=None):
            pass

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    video_path = tmp_path / "recording.ts"
    capture_recording_thumbnail(video_path)

    assert instances[0].played == str(video_path)
    assert instances[0].terminated is True


def test_capture_recording_thumbnail_returns_none_when_no_frame_produced(tmp_path, monkeypatch):
    class _FakeMPV(_FakeMPVBase):
        def wait_for_playback(self, timeout=None):
            pass  # simulates a missing/corrupt file -- no image file ever written

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    assert capture_recording_thumbnail(tmp_path / "recording.ts") is None


def test_capture_recording_thumbnail_returns_none_and_terminates_on_timeout(tmp_path, monkeypatch):
    instances = []

    class _FakeMPV(_FakeMPVBase):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

        def wait_for_playback(self, timeout=None):
            raise TimeoutError("simulated hang")

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    assert capture_recording_thumbnail(tmp_path / "recording.ts") is None
    assert instances[0].terminated is True


def test_live_buffer_mpv_options_enables_seekable_cache():
    assert live_buffer_mpv_options(10)["demuxer_seekable_cache"] == "yes"
