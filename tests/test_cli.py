import logging
from datetime import datetime, timedelta, timezone

from tvdinner.bookmarks import Bookmark
from tvdinner.cli import (
    _make_epg_progress_reporter,
    format_channel_line,
    hd_first,
    main,
    now_and_next_text,
    recording_filename,
    run_bookmarks_command,
    run_mpv_command,
    schedule_window,
    select_channel,
    stream_quality_badges,
)
from tvdinner.epg import Epg, EpgDisplay, Programme
from tvdinner.m3u import Channel, Playlist
from tvdinner.player import StreamInfo
from tvdinner.plex import PlexNode
from tvdinner.schedule import ScheduledRecording
from tvdinner.tmdb import MovieMetadata

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


def test_hd_first_moves_hd_channels_to_the_front():
    sd = Channel(name="BBC ONE Scot", url="http://x/1")
    hd = Channel(name="BBC 1 Scot HD", url="http://x/101")
    other = Channel(name="ITV3", url="http://x/10")
    assert [c.name for c in hd_first([sd, other, hd])] == ["BBC 1 Scot HD", "BBC ONE Scot", "ITV3"]


def test_hd_first_is_stable_within_each_group():
    a = Channel(name="A HD", url="http://x/a")
    b = Channel(name="B HD", url="http://x/b")
    assert [c.name for c in hd_first([a, b])] == ["A HD", "B HD"]


def test_main_hdhomerun_default_channel_prefers_hd(tmp_path, monkeypatch):
    # The channel a bare `tvdinner hdhomerun://...` (no --channel) starts
    # on should match what the guide now shows first -- the HD variant,
    # not just whichever happened to sort first in the raw lineup.
    sd = Channel(name="BBC ONE Scot", url="http://192.168.1.50:5004/auto/v1", tvg_id="1")
    hd = Channel(name="BBC 1 Scot HD", url="http://192.168.1.50:5004/auto/v101", tvg_id="101")
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[sd, hd]), None)
    )

    played = {}

    def fake_play_stream(url, **kwargs):
        played["url"] = url
        played["channel"] = kwargs.get("channel")
        return 0

    monkeypatch.setattr("tvdinner.cli.play_stream", fake_play_stream)

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

    assert exit_code == 0
    assert played["channel"].name == "BBC 1 Scot HD"
    assert played["url"] == "http://192.168.1.50:5004/auto/v101"


def _run_main_capturing_full_screen(tmp_path, monkeypatch, extra_args: list[str]) -> bool:
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[CHANNEL]), None)
    )

    played = {}

    def fake_play_stream(url, **kwargs):
        played["full_screen"] = kwargs.get("full_screen")
        return 0

    monkeypatch.setattr("tvdinner.cli.play_stream", fake_play_stream)

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
            *extra_args,
        ]
    )
    assert exit_code == 0
    return played["full_screen"]


def test_main_defaults_to_full_screen(tmp_path, monkeypatch):
    assert _run_main_capturing_full_screen(tmp_path, monkeypatch, []) is True


def test_main_disable_full_screen_flag(tmp_path, monkeypatch):
    assert _run_main_capturing_full_screen(tmp_path, monkeypatch, ["--disable-full-screen"]) is False


def test_main_tmdb_api_token_defaults_to_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[CHANNEL]), None)
    )
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream", lambda url, **kwargs: played.update(tmdb_api_token=kwargs.get("tmdb_api_token")) or 0
    )

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
    assert exit_code == 0
    assert played["tmdb_api_token"] is None


def test_main_threads_tmdb_api_token_flag_into_play_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[CHANNEL]), None)
    )
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream", lambda url, **kwargs: played.update(tmdb_api_token=kwargs.get("tmdb_api_token")) or 0
    )

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
            "--tmdb-api-token",
            "secret-token",
        ]
    )
    assert exit_code == 0
    assert played["tmdb_api_token"] == "secret-token"


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


def test_main_prints_loading_message_for_a_plain_m3u_url(tmp_path, monkeypatch, capsys):
    # A real playlist fetch can take several seconds (confirmed live
    # against a real-world redirect chain) -- this message is what keeps
    # that from looking like a hung terminal in the meantime.
    monkeypatch.setattr("tvdinner.cli.load_playlist", lambda url: None)

    def fail_play_stream(url, **kwargs):
        return 0

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main(
        [
            "http://example.com/playlist.m3u",
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

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Loading playlist..." in captured.err


def test_epg_progress_reporter_prints_known_total_as_a_percentage(capsys):
    report = _make_epg_progress_reporter("EPG data")

    report(50 * 1024 * 1024, 200 * 1024 * 1024)

    captured = capsys.readouterr()
    assert "25%" in captured.err
    assert "50 MB" in captured.err
    assert "200 MB" in captured.err


def test_epg_progress_reporter_prints_downloaded_only_when_total_unknown(capsys):
    # A chunked-transfer response (common for large, dynamically generated
    # XMLTV feeds) never sends a Content-Length -- confirmed live against
    # a real 400+MB feed served exactly this way.
    report = _make_epg_progress_reporter("EPG data")

    report(50 * 1024 * 1024, None)

    captured = capsys.readouterr()
    assert "50 MB downloaded" in captured.err
    assert "%" not in captured.err


def test_epg_progress_reporter_throttles_rapid_updates(capsys):
    # A large feed streams in ~1MB chunks -- without throttling this would
    # print hundreds of lines per second for a fast connection, drowning
    # out everything else on the terminal.
    report = _make_epg_progress_reporter("EPG data")

    report(1 * 1024 * 1024, None)
    report(2 * 1024 * 1024, None)
    report(3 * 1024 * 1024, None)

    captured = capsys.readouterr()
    assert captured.err.count("Loading EPG data") == 1


def test_epg_progress_reporter_also_calls_on_message(capsys):
    # play_stream mirrors this same throttled text onto the player's own
    # on-screen OSD (via player.show_text) so it doesn't look like
    # nothing's happening for anyone watching the video rather than the
    # terminal -- on_message must fire with the identical formatted text,
    # at the same throttled cadence as the terminal print.
    messages = []
    report = _make_epg_progress_reporter("EPG data", on_message=messages.append)

    report(50 * 1024 * 1024, 200 * 1024 * 1024)
    report(51 * 1024 * 1024, 200 * 1024 * 1024)  # throttled -- should not add a second message

    captured = capsys.readouterr()
    assert messages == [captured.err.strip()]


_PLEX_ARGS = [
    "plex://192.168.0.218:32400?X-Plex-Token=abcdef123456",
    "--no-log",
]


def _plex_paths(tmp_path):
    return [
        "--epg-shifts",
        str(tmp_path / "epg_shifts.json"),
        "--favorites",
        str(tmp_path / "favorites.json"),
        "--schedule-file",
        str(tmp_path / "schedule.json"),
        "--playback-positions-file",
        str(tmp_path / "playback_positions.json"),
    ]


def test_main_invalid_plex_url_prints_usage_error(tmp_path, capsys):
    exit_code = main(["plex://192.168.0.218:32400", "--no-log", *_plex_paths(tmp_path)])  # missing X-Plex-Token

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Invalid plex:// URL" in captured.err


def test_main_reports_plex_error_without_falling_back_to_raw_stream(tmp_path, monkeypatch, capsys):
    # A plex:// source that fails to connect must be reported as an error,
    # not silently retried as a direct stream URL, same reasoning as the
    # xtream:// case above.
    monkeypatch.setattr("tvdinner.cli.list_plex_libraries", lambda creds: ([], "boom"))

    def fail_play_stream(*args, **kwargs):
        raise AssertionError("play_stream should not be called when the Plex source fails to load")

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main([*_PLEX_ARGS, *_plex_paths(tmp_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Plex error: boom" in captured.err
    assert "abcdef123456" not in captured.err


def test_main_reports_no_plex_libraries(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tvdinner.cli.list_plex_libraries", lambda creds: ([], None))

    def fail_play_stream(*args, **kwargs):
        raise AssertionError("play_stream should not be called when there are no Plex libraries")

    monkeypatch.setattr("tvdinner.cli.play_stream", fail_play_stream)

    exit_code = main([*_PLEX_ARGS, *_plex_paths(tmp_path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "No movie or TV libraries found" in captured.err


def test_main_plex_url_calls_play_stream_with_root_nodes(tmp_path, monkeypatch):
    nodes = [PlexNode(rating_key="1", title="Movies", kind="library_movie", subtitle="Movies")]
    monkeypatch.setattr("tvdinner.cli.list_plex_libraries", lambda creds: (nodes, None))

    captured_kwargs = {}

    def fake_play_stream(url, **kwargs):
        captured_kwargs["url"] = url
        captured_kwargs.update(kwargs)
        return 0

    monkeypatch.setattr("tvdinner.cli.play_stream", fake_play_stream)

    exit_code = main([*_PLEX_ARGS, *_plex_paths(tmp_path)])

    assert exit_code == 0
    # The base URL, never the raw token-bearing URL, is passed as `url` --
    # defense in depth so the token can never leak into play_stream's own
    # "Starting playback" log line.
    assert captured_kwargs["url"] == "http://192.168.0.218:32400"
    assert captured_kwargs["plex_root_nodes"] == nodes


def test_run_bookmarks_command_redacts_plex_credentials_in_log(monkeypatch, caplog):
    bookmark = Bookmark(name="My Plex", url="plex://192.168.0.218:32400?X-Plex-Token=abcdef123456")
    monkeypatch.setattr("tvdinner.cli.run_bookmarks_tui", lambda path: (bookmark, False))

    captured_argv = []
    monkeypatch.setattr("tvdinner.cli.main", lambda argv: captured_argv.append(argv) or 0)

    with caplog.at_level(logging.INFO):
        exit_code = run_bookmarks_command(["--no-log"])

    assert exit_code == 0
    assert captured_argv == [["plex://192.168.0.218:32400?X-Plex-Token=abcdef123456", "--no-log"]]
    assert "abcdef123456" not in caplog.text
    assert "plex://192.168.0.218:32400?X-Plex-Token=abcd***" in caplog.text


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


def test_run_bookmarks_command_passes_and_redacts_tmdb_token(monkeypatch, caplog):
    bookmark = Bookmark(name="My Provider", url="http://example.com/playlist.m3u", tmdb_api_token="secret-tmdb-token")
    monkeypatch.setattr("tvdinner.cli.run_bookmarks_tui", lambda path: (bookmark, False))

    captured_argv = []
    monkeypatch.setattr("tvdinner.cli.main", lambda argv: captured_argv.append(argv) or 0)

    with caplog.at_level(logging.INFO):
        exit_code = run_bookmarks_command(["--no-log"])

    assert exit_code == 0
    # main() still gets the real, unredacted token -- only the log line is redacted.
    assert captured_argv == [
        ["http://example.com/playlist.m3u", "--tmdb-api-token", "secret-tmdb-token", "--no-log"]
    ]
    assert "secret-tmdb-token" not in caplog.text
    assert "--tmdb-api-token', '***'" in caplog.text


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


def test_run_mpv_command_reports_missing_file(tmp_path, capsys):
    exit_code = run_mpv_command([str(tmp_path / "does-not-exist.mkv"), "--no-log"])
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_run_mpv_command_guesses_title_and_year_from_filename(tmp_path, monkeypatch):
    video = tmp_path / "His Girl Friday (1940).webm"
    video.write_bytes(b"")

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(url=url, **kwargs) or 0)

    exit_code = run_mpv_command(
        [str(video), "--no-log", "--playback-positions-file", str(tmp_path / "positions.json")]
    )

    assert exit_code == 0
    assert played["url"] == str(video)
    assert played["title"] == "His Girl Friday"
    assert played["initial_vod_item"].title == "His Girl Friday"
    assert played["initial_vod_item"].year == "1940"
    assert played["initial_vod_item"].url == str(video)
    assert played["vod_metadata_loader"] is None  # no --tmdb-api-token given


def test_run_mpv_command_title_and_year_flags_override_the_guess(tmp_path, monkeypatch):
    video = tmp_path / "ambiguous_filename.mkv"
    video.write_bytes(b"")

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = run_mpv_command(
        [
            str(video),
            "--no-log",
            "--playback-positions-file",
            str(tmp_path / "positions.json"),
            "--title",
            "The Actual Movie",
            "--year",
            "1999",
        ]
    )

    assert exit_code == 0
    assert played["title"] == "The Actual Movie"
    assert played["initial_vod_item"].title == "The Actual Movie"
    assert played["initial_vod_item"].year == "1999"


def test_run_mpv_command_vod_metadata_loader_fetches_and_builds_vod_item(tmp_path, monkeypatch):
    video = tmp_path / "His Girl Friday (1940).webm"
    video.write_bytes(b"")

    captured_lookup = {}

    def fake_fetch(title, year, api_token, *args, **kwargs):
        captured_lookup.update(title=title, year=year, api_token=api_token)
        return MovieMetadata(
            title="His Girl Friday",
            year="1940",
            poster_url="https://image.tmdb.org/t/p/w500/abc.jpg",
            overview="A newspaper editor and his ace reporter ex-wife.",
            rating="8.0",
        )

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = run_mpv_command(
        [
            str(video),
            "--no-log",
            "--playback-positions-file",
            str(tmp_path / "positions.json"),
            "--tmdb-api-token",
            "secret-token",
        ]
    )

    assert exit_code == 0
    loader = played["vod_metadata_loader"]
    assert loader is not None
    enriched = loader()
    assert captured_lookup == {"title": "His Girl Friday", "year": "1940", "api_token": "secret-token"}
    assert enriched.title == "His Girl Friday"
    assert enriched.url == str(video)
    assert enriched.poster_url == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert enriched.description == "A newspaper editor and his ace reporter ex-wife."
    assert enriched.rating == "8.0"


def test_run_mpv_command_vod_metadata_loader_returns_none_without_a_tmdb_match(tmp_path, monkeypatch):
    video = tmp_path / "His Girl Friday (1940).webm"
    video.write_bytes(b"")

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", lambda *a, **k: None)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = run_mpv_command(
        [
            str(video),
            "--no-log",
            "--playback-positions-file",
            str(tmp_path / "positions.json"),
            "--tmdb-api-token",
            "secret-token",
        ]
    )

    assert exit_code == 0
    assert played["vod_metadata_loader"]() is None


def test_main_dispatches_mpv_subcommand(tmp_path, monkeypatch):
    video = tmp_path / "Some Movie (2001).mkv"
    video.write_bytes(b"")

    captured_argv = []
    monkeypatch.setattr("tvdinner.cli.run_mpv_command", lambda argv: captured_argv.append(argv) or 0)

    exit_code = main(["mpv", str(video), "--no-log"])
    assert exit_code == 0
    assert captured_argv == [[str(video), "--no-log"]]
