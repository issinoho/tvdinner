import threading
from datetime import datetime
from pathlib import Path

import pytest

from tvdinner.player import (
    Player,
    StallTracker,
    _TerminationGuardedMPV,
    _format_bitrate,
    _format_channels,
    _format_container,
    _format_fps,
    _hdr_label,
    _short_codec_name,
    capture_video_thumbnail,
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


def test_capture_video_thumbnail_returns_produced_jpeg_bytes(tmp_path, monkeypatch):
    class _FakeMPV(_FakeMPVBase):
        def wait_for_playback(self, timeout=None):
            outdir = Path(self.kwargs["vo_image_outdir"])
            (outdir / "00000001.jpg").write_bytes(b"fake-jpeg-bytes")

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    assert capture_video_thumbnail(tmp_path / "recording.ts") == b"fake-jpeg-bytes"


def test_capture_video_thumbnail_plays_the_given_path_and_always_terminates(tmp_path, monkeypatch):
    instances = []

    class _FakeMPV(_FakeMPVBase):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

        def wait_for_playback(self, timeout=None):
            pass

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    video_path = tmp_path / "recording.ts"
    capture_video_thumbnail(video_path)

    assert instances[0].played == str(video_path)
    assert instances[0].terminated is True


def test_capture_video_thumbnail_accepts_a_remote_stream_url(monkeypatch):
    # A Plex chapter thumbnail's fallback grabs a frame from the resolved
    # (remote, token-bearing) stream URL, not a local Path -- confirm a
    # plain string source works the same way a Path does (source is just
    # handed to mpv's own play() as str(source) either way).
    instances = []

    class _FakeMPV(_FakeMPVBase):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

        def wait_for_playback(self, timeout=None):
            pass

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    url = "http://192.168.0.218:32400/library/parts/10/123/file.mkv?X-Plex-Token=abcdefgh12345678"
    capture_video_thumbnail(url, seek_seconds=120.0)

    assert instances[0].played == url
    assert instances[0].kwargs["start"] == 120.0


def test_capture_video_thumbnail_redacts_a_credential_bearing_source_on_failure(monkeypatch, caplog):
    class _FakeMPV(_FakeMPVBase):
        def wait_for_playback(self, timeout=None):
            raise TimeoutError("simulated hang")

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    url = "http://192.168.0.218:32400/library/parts/10/123/file.mkv?X-Plex-Token=abcdefgh12345678"
    with caplog.at_level("WARNING"):
        capture_video_thumbnail(url)

    assert "abcdefgh12345678" not in caplog.text


def test_capture_video_thumbnail_returns_none_when_no_frame_produced(tmp_path, monkeypatch):
    class _FakeMPV(_FakeMPVBase):
        def wait_for_playback(self, timeout=None):
            pass  # simulates a missing/corrupt file -- no image file ever written

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    assert capture_video_thumbnail(tmp_path / "recording.ts") is None


def test_capture_video_thumbnail_returns_none_and_terminates_on_timeout(tmp_path, monkeypatch):
    instances = []

    class _FakeMPV(_FakeMPVBase):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

        def wait_for_playback(self, timeout=None):
            raise TimeoutError("simulated hang")

    monkeypatch.setattr("tvdinner.player.mpv.MPV", _FakeMPV)

    assert capture_video_thumbnail(tmp_path / "recording.ts") is None
    assert instances[0].terminated is True


def test_live_buffer_mpv_options_enables_seekable_cache():
    assert live_buffer_mpv_options(10)["demuxer_seekable_cache"] == "yes"


class _FakeKeyMPV:
    def on_key_press(self, keydef):
        return lambda callback: callback

    def key_binding(self, keydef):
        return lambda handler: handler

    def unregister_key_binding(self, keydef):
        pass


def _player_with_fake_keys():
    player = Player.__new__(Player)
    player._mpv = _FakeKeyMPV()
    player._bound_keys = set()
    return player


def test_is_key_bound_tracks_on_key_press_and_unbind_key():
    player = _player_with_fake_keys()
    assert not player.is_key_bound("ESC")

    player.on_key_press("ESC", lambda: None)
    assert player.is_key_bound("ESC")

    player.unbind_key("ESC")
    assert not player.is_key_bound("ESC")


def test_is_key_bound_tracks_on_key_press_or_hold():
    player = _player_with_fake_keys()

    player.on_key_press_or_hold("MENU", on_press=lambda: None, on_hold=lambda: None)

    assert player.is_key_bound("MENU")
    assert not player.is_key_bound("ESC")


def test_stall_tracker_fires_after_threshold_of_no_movement():
    tracker = StallTracker(15.0)
    assert not tracker.update(100.0, now=0.0)
    assert not tracker.update(100.0, now=14.0)
    assert tracker.update(100.0, now=15.0)


def test_stall_tracker_moving_position_never_fires():
    tracker = StallTracker(15.0)
    for second in range(0, 60, 3):
        assert not tracker.update(float(second), now=float(second))


def test_stall_tracker_restarts_count_after_firing():
    tracker = StallTracker(15.0)
    tracker.update(5.0, now=0.0)
    assert tracker.update(5.0, now=15.0)
    assert not tracker.update(5.0, now=18.0)
    assert not tracker.update(5.0, now=32.0)
    assert tracker.update(5.0, now=33.0)


def test_stall_tracker_pause_and_unloaded_reset_the_count():
    tracker = StallTracker(15.0)
    tracker.update(5.0, now=0.0)
    assert not tracker.update(5.0, now=10.0, paused=True)
    assert not tracker.update(5.0, now=20.0)  # count restarts once unpaused
    assert not tracker.update(None, now=40.0)
    assert not tracker.update(5.0, now=41.0)
    assert tracker.update(5.0, now=56.0)


def test_stall_tracker_reports_whether_the_stream_played_before_freezing():
    from tvdinner.player import StallTracker

    tracker = StallTracker(threshold=8.0)
    tracker.update(-1.0, 0.0)  # loaded, no position yet
    tracker.update(0.5, 2.0)
    tracker.update(20.0, 22.0)
    assert tracker.update(20.0, 26.0) is False
    assert tracker.update(20.0, 30.0) is True
    assert tracker.played_before_stall is True


def test_stall_tracker_never_playing_is_not_played_before_stall():
    from tvdinner.player import StallTracker

    tracker = StallTracker(threshold=8.0)
    tracker.update(-1.0, 0.0)
    assert tracker.update(-1.0, 8.0) is True
    assert tracker.played_before_stall is False
    # Loaded-but-no-position moving to a first real position isn't playback either.
    tracker.update(-1.0, 10.0)
    tracker.update(0.0, 12.0)
    assert tracker.update(0.0, 20.0) is True
    assert tracker.played_before_stall is False


class _RecordingRawMPV:
    """Stands in for a real mpv.MPV: records every access, and like the
    real one, terminate() leaves it unusable -- here by failing the test
    rather than segfaulting."""

    def __init__(self):
        self.calls = []
        self.terminated = False
        self.core_shutdown = False
        self.pause = False

    def _check_alive(self):
        assert not self.terminated, "libmpv handle used after terminate()"

    def command(self, *args):
        self._check_alive()
        self.calls.append(args)
        return "ok"

    def terminate(self):
        self.terminated = True

    def wait_for_playback(self):
        return "waited"


def test_termination_guard_forwards_reads_writes_and_calls_while_open():
    raw = _RecordingRawMPV()
    guard = _TerminationGuardedMPV(raw)

    assert guard.command("overlay-remove", "0") == "ok"
    guard.pause = True

    assert raw.calls == [("overlay-remove", "0")]
    assert guard.pause is True


def test_termination_guard_drops_everything_after_terminate(caplog):
    raw = _RecordingRawMPV()
    guard = _TerminationGuardedMPV(raw)
    guard.terminate()

    with caplog.at_level("INFO", logger="tvdinner.player"):
        assert guard.command("overlay-remove", "0") is None
        assert guard.pause is None
        guard.pause = True

    assert raw.terminated
    assert raw.calls == []
    assert raw.pause is False
    assert "Ignored mpv call command() after shutdown" in caplog.text
    assert "Ignored mpv write of 'pause' after shutdown" in caplog.text


def test_termination_guard_drops_a_method_fetched_before_terminate_but_called_after():
    """The 2026-11-21 crash shape: a Timer holding player.clear_overlay
    whose callback only reaches mpv.command once quit() has run."""
    raw = _RecordingRawMPV()
    guard = _TerminationGuardedMPV(raw)
    command = guard.command

    guard.terminate()

    assert command("overlay-remove", "0") is None
    assert raw.calls == []


def test_termination_guard_terminate_waits_for_an_in_flight_call():
    raw = _RecordingRawMPV()
    inside = threading.Event()
    release = threading.Event()

    def slow_command(*args):
        inside.set()
        release.wait(5)
        raw._check_alive()
        raw.calls.append(args)

    raw.command = slow_command
    guard = _TerminationGuardedMPV(raw)

    worker = threading.Thread(target=guard.command, args=("show-text", "hi"))
    worker.start()
    assert inside.wait(5)

    terminator = threading.Thread(target=guard.terminate)
    terminator.start()
    terminator.join(0.2)
    assert terminator.is_alive(), "terminate() must wait for the in-flight call"
    assert not raw.terminated

    release.set()
    worker.join(5)
    terminator.join(5)
    assert raw.calls == [("show-text", "hi")]
    assert raw.terminated


def test_termination_guard_does_not_hold_its_lock_while_destroying():
    """python-mpv's terminate() joins the event thread; a callback there
    touching the guard must get a no-op, not deadlock against it."""
    raw = _RecordingRawMPV()
    guard = _TerminationGuardedMPV(raw)
    results = []

    def terminate_joining_event_thread():
        event_thread = threading.Thread(target=lambda: results.append(guard.command("overlay-remove", "0")))
        event_thread.start()
        event_thread.join(5)
        assert not event_thread.is_alive(), "event-thread callback deadlocked on the guard"
        raw.terminated = True

    raw.terminate = terminate_joining_event_thread
    guard.terminate()

    assert results == [None]
    assert raw.calls == []


def test_termination_guard_wait_and_core_shutdown_bypass_the_lock():
    raw = _RecordingRawMPV()
    guard = _TerminationGuardedMPV(raw)
    guard._lock.acquire()
    try:
        result = []
        reader = threading.Thread(target=lambda: result.append((guard.wait_for_playback(), guard.core_shutdown)))
        reader.start()
        reader.join(2)
        assert result == [("waited", False)]
    finally:
        guard._lock.release()

    guard.terminate()
    assert guard.core_shutdown is True


def test_termination_guard_terminate_is_idempotent():
    raw = _RecordingRawMPV()
    terminations = []
    raw.terminate = lambda: terminations.append(1)
    guard = _TerminationGuardedMPV(raw)

    guard.terminate()
    guard.terminate()

    assert terminations == [1]
