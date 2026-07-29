import logging
from datetime import datetime, timedelta, timezone

from tvdinner.bookmarks import Bookmark
from tvdinner.cli import (
    format_channel_line,
    main,
    now_and_next_text,
    recording_filename,
    run_bookmarks_command,
    schedule_window,
    select_channel,
    stream_quality_badges,
)
from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel
from tvdinner.player import StreamInfo
from tvdinner.schedule import ScheduledRecording

CHANNEL = Channel(name="Demo News", url="http://stream/demo", tvg_id="demo.news", group_title="Test")


def _epg_with_current_and_next(now: datetime, description: str | None = None) -> Epg:
    epg = Epg()
    epg.programmes["demo.news"] = [
        Programme(
            channel_id="demo.news",
            start=now - timedelta(minutes=10),
            stop=now + timedelta(minutes=20),
            title="Live Test Broadcast",
            description=description,
        ),
        Programme(
            channel_id="demo.news",
            start=now + timedelta(minutes=20),
            stop=now + timedelta(minutes=50),
            title="Upcoming Test Show",
        ),
    ]
    return epg


def test_now_and_next_text_without_epg_returns_none():
    assert now_and_next_text(CHANNEL, None, None, datetime.now(timezone.utc)) == (None, None)


def test_now_and_next_text_without_tvg_id_returns_none():
    channel = Channel(name="No ID", url="http://stream/x")
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay()
    assert now_and_next_text(channel, epg, display, now) == (None, None)


def test_now_and_next_text_formats_current_and_upcoming():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay(timezone=timezone.utc)

    now_text, next_text = now_and_next_text(CHANNEL, epg, display, now)
    assert now_text.startswith("Now: Live Test Broadcast (")
    assert next_text.startswith("Next: Upcoming Test Show (")


def test_format_channel_line_includes_group_and_epg():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now)
    display = EpgDisplay(timezone=timezone.utc)

    line = format_channel_line(1, CHANNEL, 2, epg, display, now)
    assert line.startswith(" 1. Demo News [Test]")
    assert "Now: Live Test Broadcast" in line
    assert "Next: Upcoming Test Show" in line


def test_format_channel_line_without_epg_data():
    now = datetime.now(timezone.utc)
    line = format_channel_line(1, CHANNEL, 1, None, None, now)
    assert line == "1. Demo News [Test]"


def test_format_channel_line_does_not_include_description():
    now = datetime.now(timezone.utc)
    epg = _epg_with_current_and_next(now, description="Should not appear in the compact list line.")
    display = EpgDisplay(timezone=timezone.utc)

    line = format_channel_line(1, CHANNEL, 1, epg, display, now)
    assert "Should not appear" not in line


def test_select_channel_by_index():
    channels = [CHANNEL, Channel(name="Other", url="http://stream/other")]
    assert select_channel(channels, "2").name == "Other"


def test_select_channel_by_name_substring():
    channels = [CHANNEL, Channel(name="Other", url="http://stream/other")]
    assert select_channel(channels, "demo") is CHANNEL


def test_select_channel_not_found():
    channels = [CHANNEL]
    assert select_channel(channels, "nope") is None


def test_stream_quality_badges_returns_empty_list_without_info():
    assert stream_quality_badges(None) == []


def test_stream_quality_badges_omits_missing_fields():
    info = StreamInfo(resolution="1080p", video_codec="H.264", audio_codec="AAC")
    assert stream_quality_badges(info) == ["1080p", "H.264", "AAC"]


def test_stream_quality_badges_includes_everything_present():
    info = StreamInfo(
        resolution="4K",
        video_codec="HEVC",
        fps="59.94fps",
        hdr="HDR10",
        audio_codec="AC-3",
        audio_channels="5.1",
    )
    assert stream_quality_badges(info) == ["4K", "HEVC", "59.94fps", "HDR10", "AC-3", "5.1"]


def test_recording_filename_keeps_spaces_in_channel_names():
    now = datetime(2026, 7, 26, 14, 30, 5)
    assert recording_filename("BBC One", now) == "BBC One_20260726-143005.ts"


def test_recording_filename_strips_path_separators_and_symbols():
    now = datetime(2026, 7, 26, 14, 30, 5)
    assert recording_filename("http://example.com/stream?x=1", now) == "http___example.com_stream_x_1_20260726-143005.ts"


def test_recording_filename_falls_back_to_stream_for_empty_label():
    now = datetime(2026, 7, 26, 14, 30, 5)
    assert recording_filename("###", now) == "stream_20260726-143005.ts"


def test_schedule_window_applies_no_shift_by_default():
    display = EpgDisplay(timezone=timezone.utc)
    entry = ScheduledRecording.create(
        channel_url="http://x/tcm",
        channel_name="TCM US West",
        title="World Without End",
        start=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc),
        stop=datetime(2026, 7, 26, 14, 15, tzinfo=timezone.utc),
    )
    start, stop = schedule_window(entry, display)
    assert start == entry.start
    assert stop == entry.stop


def test_schedule_window_applies_per_channel_shift():
    # Regression test: scheduling/polling used to compare a programme's raw
    # (unshifted) start/stop directly against real time, so a channel with
    # a non-zero --epg-shifts entry (e.g. the README's own "TCM US West":
    # "-3h" example) would be scheduled/started/stopped hours off from
    # when the guide actually said it airs -- or, for the "already ended"
    # check specifically, could reject a programme that hadn't started yet.
    display = EpgDisplay(timezone=timezone.utc, channel_shifts={"TCM US West": timedelta(hours=-3)})
    entry = ScheduledRecording.create(
        channel_url="http://x/tcm",
        channel_name="TCM US West",
        title="World Without End",
        start=datetime(2026, 7, 26, 15, 30, tzinfo=timezone.utc),
        stop=datetime(2026, 7, 26, 17, 15, tzinfo=timezone.utc),
    )
    start, stop = schedule_window(entry, display)
    assert start == datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
    assert stop == datetime(2026, 7, 26, 14, 15, tzinfo=timezone.utc)


def test_main_reports_xtream_error_without_falling_back_to_raw_stream(tmp_path, monkeypatch, capsys):
    # An xtream:// source that fails to load must be reported as an error,
    # not silently retried as a direct stream URL (which mpv could never
    # play anyway) the way a genuinely non-M3U http(s) URL is.
    monkeypatch.setattr("tvdinner.cli.load_xtream_playlist", lambda creds: (None, "boom"))

    def fail_play_stream(*args, **kwargs):
        raise AssertionError("play_stream should not be called when the Xtream source fails to load")

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main(
        [
            "xtream://myuser:mypass@panel.example.com:8080",
            "--no-log",
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--favorites",
            str(tmp_path / "favorites.json"),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "playback_positions.json"),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Xtream error: boom" in captured.err
    assert "mypass" not in captured.err


def test_main_reports_stalker_error_without_falling_back_to_raw_stream(tmp_path, monkeypatch, capsys):
    # A stalker:// source that fails to load must be reported as an error,
    # not silently retried as a direct stream URL, same reasoning as the
    # xtream:// case above.
    monkeypatch.setattr("tvdinner.cli.load_stalker_playlist", lambda creds: (None, "boom"))

    def fail_play_stream(*args, **kwargs):
        raise AssertionError("play_stream should not be called when the Stalker source fails to load")

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main(
        [
            "stalker://panel.example.com:8080/c/?mac=AA:BB:CC:DD:EE:FF",
            "--no-log",
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--favorites",
            str(tmp_path / "favorites.json"),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "playback_positions.json"),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Stalker error: boom" in captured.err


def test_main_reports_hdhomerun_error_without_falling_back_to_raw_stream(tmp_path, monkeypatch, capsys):
    # An hdhomerun:// source that fails to load must be reported as an
    # error, not silently retried as a direct stream URL, same reasoning
    # as the xtream:///stalker:// cases above.
    monkeypatch.setattr("tvdinner.cli.load_hdhomerun_playlist", lambda target: (None, "boom"))

    def fail_play_stream(*args, **kwargs):
        raise AssertionError("play_stream should not be called when the HDHomeRun source fails to load")

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main(
        [
            "hdhomerun://192.168.1.50",
            "--no-log",
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--favorites",
            str(tmp_path / "favorites.json"),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "playback_positions.json"),
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "HDHomeRun error: boom" in captured.err


def test_run_bookmarks_command_redacts_xtream_credentials_in_log(monkeypatch, caplog):
    # Selecting a bookmark used to log its raw URL unredacted -- a real
    # credential leak for an xtream://user:pass@host bookmark, even though
    # main() itself has always redacted this same URL in its own logging.
    bookmark = Bookmark(name="My Xtream", url="xtream://myuser:mypass@panel.example.com:8080")
    monkeypatch.setattr("tvdinner.cli.run_bookmarks_tui", lambda path: (bookmark, False))

    captured_argv = []
    monkeypatch.setattr("tvdinner.cli.main", lambda argv: captured_argv.append(argv) or 0)

    with caplog.at_level(logging.INFO):
        exit_code = run_bookmarks_command(["--no-log"])

    assert exit_code == 0
    # main() still gets the real, unredacted URL -- only the log line is redacted.
    assert captured_argv == [["xtream://myuser:mypass@panel.example.com:8080", "--no-log"]]
    assert "mypass" not in caplog.text
    assert "xtream://myuser:***@panel.example.com:8080" in caplog.text


def test_main_strips_wrapping_quotes_from_a_pasted_url(tmp_path, monkeypatch):
    # Regression test: this project's own docs show URLs shell-quoted
    # (e.g. tvdinner 'hdhomerun://192.168.0.11'), and a user who pastes
    # that whole example into a bookmark's URL field (not a shell, so the
    # quotes are never stripped) ends up with a literal leading/trailing
    # quote character baked into the URL -- which broke scheme detection
    # entirely, silently falling through to "treat as a direct stream".
    captured_targets = []

    def fake_load_hdhomerun_playlist(target):
        captured_targets.append(target)
        return None, "boom"

    monkeypatch.setattr("tvdinner.cli.load_hdhomerun_playlist", fake_load_hdhomerun_playlist)

    def fail_play_stream(*args, **kwargs):
        raise AssertionError("play_stream should not be called -- the quoted URL must still be recognized")

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main(
        [
            "'hdhomerun://192.168.0.11'",
            "--no-log",
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--favorites",
            str(tmp_path / "favorites.json"),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "playback_positions.json"),
        ]
    )

    assert exit_code == 1
    assert len(captured_targets) == 1
    assert captured_targets[0].base_url == "http://192.168.0.11"
