import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from tvdinner.bookmarks import Bookmark
from tvdinner.channel_logos import CHANNELS_URL, LOGOS_URL
from tvdinner.cli import (
    _dir_size,
    _format_cache_bytes,
    _format_stats_duration,
    _make_epg_progress_reporter,
    _period_starts,
    _plex_title_logo_target,
    _PlexNavFrame,
    _print_stats_table,
    _top_channels,
    _watch_seconds_by_kind,
    format_channel_line,
    hd_first,
    main,
    now_and_next_text,
    recording_filename,
    run_backup_command,
    run_bookmarks_command,
    run_clear_tmdb_command,
    run_gdrive_login_command,
    run_gdrive_logout_command,
    run_hard_reset_command,
    run_restore_command,
    run_stats_command,
    run_store_tmdb_command,
    schedule_window,
    select_channel,
    stream_quality_badges,
)
from tvdinner.epg import Epg, EpgDisplay, Programme, cache_path_for, parsed_cache_path_for
from tvdinner.gdrive import BUNDLED_CLIENT_ID, BUNDLED_CLIENT_SECRET, GdriveError
from tvdinner.history import HistoryEntry, append_history_entry
from tvdinner.m3u import Channel, Playlist
from tvdinner.player import StreamInfo
from tvdinner.plex import PlexNode
from tvdinner.schedule import ScheduledRecording
from tvdinner.tmdb import MovieMetadata
from tvdinner.xtream import XtreamCreds, xtream_epg_url
from tvdinner.youtube import YoutubeInfo

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
            "--tmdb-token-file",
            str(tmp_path / "tmdb_token.json"),
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


def test_main_threads_history_file_flag_into_play_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[CHANNEL]), None)
    )
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream", lambda url, **kwargs: played.update(history_path=kwargs.get("history_path")) or 0
    )
    history_path = tmp_path / "history.jsonl"

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
            "--history-file",
            str(history_path),
        ]
    )
    assert exit_code == 0
    assert played["history_path"] == history_path


def test_main_no_history_flag_disables_history(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[CHANNEL]), None)
    )
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream", lambda url, **kwargs: played.update(history_path=kwargs.get("history_path")) or 0
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
            "--no-history",
        ]
    )
    assert exit_code == 0
    assert played["history_path"] is None


def test_main_threads_playlist_source_into_play_stream(tmp_path, monkeypatch):
    # For a channel-backed session, playlist_source is the redacted URL
    # the user actually launched with -- here a plain HDHomeRun URL with
    # nothing to redact.
    monkeypatch.setattr(
        "tvdinner.cli.load_hdhomerun_playlist", lambda target: (Playlist(channels=[CHANNEL]), None)
    )
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream",
        lambda url, **kwargs: played.update(playlist_source=kwargs.get("playlist_source")) or 0,
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
    assert played["playlist_source"] == "hdhomerun://192.168.1.50"


def test_main_favorites_feed_is_not_the_raw_xtream_login_url(tmp_path, monkeypatch):
    # favorites.json must never be keyed by the raw source string for an
    # Xtream login -- that's a real username/password sitting in the
    # file. See tvdinner.redact.stable_credential_key.
    monkeypatch.setattr("tvdinner.cli.load_xtream_playlist", lambda creds: (Playlist(channels=[CHANNEL]), None))
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream",
        lambda url, **kwargs: played.update(favorites_feed=kwargs.get("favorites_feed")) or 0,
    )

    exit_code = main(
        [
            "xtream://myuser:hunter2@panel.example.com:8080",
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
    assert played["favorites_feed"] is not None
    assert "hunter2" not in played["favorites_feed"]
    assert played["favorites_feed"] != "xtream://myuser:hunter2@panel.example.com:8080"


def test_main_migrates_favorites_off_a_legacy_raw_url_key(tmp_path, monkeypatch):
    # A favorites.json saved before the fix above existed is still keyed
    # by the raw, credential-bearing source string -- confirm it gets
    # migrated onto the safe key (and the leaked credential scrubbed from
    # disk), not silently dropped.
    from tvdinner.favorites import save_favorites

    favorites_path = tmp_path / "favorites.json"
    legacy_feed = "xtream://myuser:hunter2@panel.example.com:8080"
    save_favorites(favorites_path, legacy_feed, {"BBC One"})

    monkeypatch.setattr("tvdinner.cli.load_xtream_playlist", lambda creds: (Playlist(channels=[CHANNEL]), None))
    played = {}
    monkeypatch.setattr(
        "tvdinner.cli.play_stream",
        lambda url, **kwargs: played.update(favorites=kwargs.get("favorites"), favorites_feed=kwargs.get("favorites_feed"))
        or 0,
    )

    exit_code = main(
        [
            legacy_feed,
            "--no-log",
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--favorites",
            str(favorites_path),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "playback_positions.json"),
        ]
    )

    assert exit_code == 0
    assert played["favorites"] == {"BBC One"}  # migrated, not lost

    on_disk = json.loads(favorites_path.read_text())
    assert legacy_feed not in on_disk  # the old, credential-bearing key is gone
    assert "hunter2" not in favorites_path.read_text()
    assert on_disk[played["favorites_feed"]] == ["BBC One"]


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


def test_main_with_no_arguments_dispatches_to_bookmarks(monkeypatch):
    # A bare `tvdinner` (no URL, no subcommand) has nothing for argparse's
    # required `url` positional to bind to -- rather than the usual
    # "the following arguments are required: url" error, this is treated
    # the same as `tvdinner bookmarks`, since picking from what's already
    # saved is the natural thing to want with no arguments at all.
    captured_argv = []
    monkeypatch.setattr("tvdinner.cli.run_bookmarks_command", lambda argv: captured_argv.append(argv) or 0)

    exit_code = main([])

    assert exit_code == 0
    assert captured_argv == [[]]


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


def test_run_bookmarks_command_launches_a_local_video_file_bookmark_with_tmdb_metadata(tmp_path, monkeypatch):
    # A bookmark's URL field is unrestricted free text (see bookmarks.py),
    # and run_bookmarks_command re-enters the real main() with it exactly
    # as if typed directly -- so a local movie file bookmark should get
    # the same filename-guessed identity and TMDB lookup as typing
    # `tvdinner PATH --tmdb-api-token ...` would, with zero bookmark-
    # specific code for it. This exercises that end to end, rather than
    # mocking main() away like the tests above.
    video = tmp_path / "His Girl Friday (1940).webm"
    video.write_bytes(b"")

    bookmark = Bookmark(name="His Girl Friday", url=str(video), tmdb_api_token="secret-token")
    monkeypatch.setattr("tvdinner.cli.run_bookmarks_tui", lambda path: (bookmark, False))

    # main() re-entered from here uses its own DEFAULT_* config paths
    # (there's no flag on `tvdinner bookmarks` to override them) -- redirect
    # those to tmp_path so this test never touches the real ~/.config files.
    monkeypatch.setattr("tvdinner.cli.DEFAULT_CHANNEL_SHIFTS_PATH", tmp_path / "epg_shifts.json")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_FAVORITES_PATH", tmp_path / "favorites.json")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_SCHEDULE_PATH", tmp_path / "schedule.json")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_PLAYBACK_POSITIONS_PATH", tmp_path / "playback_positions.json")

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
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(url=url, **kwargs) or 0)

    exit_code = run_bookmarks_command(["--no-log"])

    assert exit_code == 0
    assert played["url"] == str(video)
    assert played["title"] == "His Girl Friday"
    assert played["initial_vod_item"].year == "1940"
    loader = played["vod_metadata_loader"]
    assert loader is not None
    enriched = loader()
    assert captured_lookup == {"title": "His Girl Friday", "year": "1940", "api_token": "secret-token"}
    assert enriched.poster_url == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert enriched.rating == "8.0"


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


def _local_video_main_argv(tmp_path, video, *extra):
    return [
        str(video),
        "--no-log",
        "--epg-shifts",
        str(tmp_path / "epg_shifts.json"),
        "--favorites",
        str(tmp_path / "favorites.json"),
        "--schedule-file",
        str(tmp_path / "schedule.json"),
        "--playback-positions-file",
        str(tmp_path / "playback_positions.json"),
        "--tmdb-token-file",
        str(tmp_path / "tmdb_token.json"),
        *extra,
    ]


def test_main_guesses_title_and_year_from_local_video_filename(tmp_path, monkeypatch):
    video = tmp_path / "His Girl Friday (1940).webm"
    video.write_bytes(b"")

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(url=url, **kwargs) or 0)

    exit_code = main(_local_video_main_argv(tmp_path, video))

    assert exit_code == 0
    assert played["url"] == str(video)
    assert played["title"] == "His Girl Friday"
    assert played["initial_vod_item"].title == "His Girl Friday"
    assert played["initial_vod_item"].year == "1940"
    assert played["initial_vod_item"].url == str(video)
    assert played["vod_metadata_loader"] is None  # no --tmdb-api-token given


def test_main_title_and_year_flags_override_the_local_video_guess(tmp_path, monkeypatch):
    video = tmp_path / "ambiguous_filename.mkv"
    video.write_bytes(b"")

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_local_video_main_argv(tmp_path, video, "--title", "The Actual Movie", "--year", "1999"))

    assert exit_code == 0
    assert played["title"] == "The Actual Movie"
    assert played["initial_vod_item"].title == "The Actual Movie"
    assert played["initial_vod_item"].year == "1999"


def test_main_local_video_vod_metadata_loader_fetches_and_builds_vod_item(tmp_path, monkeypatch):
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

    exit_code = main(_local_video_main_argv(tmp_path, video, "--tmdb-api-token", "secret-token"))

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
    assert enriched.rating_is_tmdb is True


def test_main_local_video_vod_metadata_loader_returns_none_without_a_tmdb_match(tmp_path, monkeypatch):
    video = tmp_path / "His Girl Friday (1940).webm"
    video.write_bytes(b"")

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", lambda *a, **k: None)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_local_video_main_argv(tmp_path, video, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    assert played["vod_metadata_loader"]() is None


def test_main_local_video_tmdb_lookup_tries_the_split_title_before_the_full_remainder(tmp_path, monkeypatch):
    # Regression test for a real yt-dlp download (confirmed live): a
    # leading year plus chained cast/tagline/videoID noise in the
    # filename -- naively searching TMDB with the whole year-stripped
    # remainder finds nothing, so the split first-segment candidate must
    # be tried first.
    video = tmp_path / (
        "1940 - His Girl Friday - Cary Grant and Rosalind Russell - "
        "Ex-lovers become headline hunters [wEx-z1TYPKU].webm"
    )
    video.write_bytes(b"")

    attempted_titles = []

    def fake_fetch(title, year, api_token, *args, **kwargs):
        attempted_titles.append(title)
        if title == "His Girl Friday":
            return MovieMetadata(
                title="His Girl Friday", year="1940", poster_url=None, overview="A screwball comedy.", rating="7.9"
            )
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_local_video_main_argv(tmp_path, video, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    assert played["initial_vod_item"].year == "1940"
    enriched = played["vod_metadata_loader"]()
    assert attempted_titles == ["His Girl Friday"]  # matched on the first (split) candidate -- no second attempt
    assert enriched.title == "His Girl Friday"
    assert enriched.rating == "7.9"


def test_main_treats_a_real_local_m3u_file_as_a_playlist_not_a_local_video(tmp_path, monkeypatch):
    # The local-video-file branch must not shadow the pre-existing "M3U/
    # M3U8 playlist ... or local file path" case -- a real playlist on
    # disk still needs to load as one, guessed-movie-identity machinery
    # never involved.
    playlist_path = tmp_path / "playlist.m3u"
    playlist_path.write_text('#EXTM3U\n#EXTINF:-1,Demo\nhttp://stream/demo\n')

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called for a real playlist")))

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(url=url, **kwargs) or 0)

    exit_code = main(_local_video_main_argv(tmp_path, playlist_path))

    assert exit_code == 0
    assert played.get("initial_vod_item") is None
    assert played["channel"].url == "http://stream/demo"


YOUTUBE_URL = "https://www.youtube.com/watch?v=wEx-z1TYPKU"


def _youtube_main_argv(tmp_path, *extra):
    return [
        YOUTUBE_URL,
        "--no-log",
        "--epg-shifts",
        str(tmp_path / "epg_shifts.json"),
        "--favorites",
        str(tmp_path / "favorites.json"),
        "--schedule-file",
        str(tmp_path / "schedule.json"),
        "--playback-positions-file",
        str(tmp_path / "playback_positions.json"),
        "--tmdb-token-file",
        str(tmp_path / "tmdb_token.json"),
        *extra,
    ]


def test_main_youtube_url_plays_without_a_title_override_so_mpv_sets_its_own(tmp_path, monkeypatch):
    # Unlike the local-file branch, no `title=` is passed to play_stream --
    # mpv's own yt-dlp hook is left to set the window title from the
    # resolved video's real metadata, same as it already did before this
    # feature existed.
    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(url=url, **kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path))

    assert exit_code == 0
    assert played["url"] == YOUTUBE_URL
    assert "title" not in played
    assert played["initial_vod_item"].title == "YouTube"
    assert played["initial_vod_item"].url == YOUTUBE_URL
    assert played["vod_metadata_loader"] is not None  # oEmbed is tried unconditionally, no token needed


def test_main_youtube_vod_metadata_loader_uses_oembed_without_a_tmdb_token(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(
            title="Big Buck Bunny", author_name="Blender Foundation", thumbnail_url="https://i.ytimg.com/x.jpg"
        ),
    )

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert enriched.title == "Big Buck Bunny"
    assert enriched.url == YOUTUBE_URL
    assert enriched.poster_url == "https://i.ytimg.com/x.jpg"
    assert enriched.description == "YouTube · Blender Foundation"
    assert enriched.year is None
    assert enriched.rating is None


def test_main_youtube_vod_metadata_loader_returns_none_when_oembed_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.cli.fetch_youtube_oembed", lambda url: None)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path))

    assert exit_code == 0
    assert played["vod_metadata_loader"]() is None


def test_main_youtube_still_queries_tmdb_for_a_title_with_no_year(tmp_path, monkeypatch):
    # Not gated on year presence (see the regression test below for why:
    # a real full-movie upload can easily have no year in its title at
    # all) -- but a genuine non-movie title like this one still correctly
    # finds no match, and the item is left unchanged either way.
    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="My Vacation Vlog", author_name="Someone", thumbnail_url=None),
    )

    captured_lookup = {}

    def fake_fetch(title, year, api_token, *args, **kwargs):
        captured_lookup.update(title=title, year=year)
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert captured_lookup == {"title": "My Vacation Vlog", "year": None}
    assert enriched.title == "My Vacation Vlog"  # unchanged -- TMDB found no match


def test_main_youtube_tmdb_lookup_finds_a_yearless_title_via_split_candidate(tmp_path, monkeypatch):
    # Regression test for a real official-studio upload (confirmed live
    # against youtube.com/watch?v=dCMoVmR5LZw): the title carries no year
    # anywhere, so the old year-gated lookup skipped TMDB entirely --
    # despite title_search_candidates' own separator-splitting already
    # isolating "McLintock!" as its first candidate, which TMDB resolves
    # immediately.
    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(
            title="McLintock! | FULL MOVIE | John Wayne, Maureen O'Hara | Western Rancher Cowboy Comedy",
            author_name="Shout! Studios",
            thumbnail_url="https://i.ytimg.com/y.jpg",
        ),
    )

    attempted_titles = []

    def fake_fetch(title, year, api_token, *args, **kwargs):
        attempted_titles.append(title)
        if title == "McLintock!":
            return MovieMetadata(
                title="McLintock!", year="1963", poster_url="https://image.tmdb.org/t/p/w500/mclintock.jpg", overview="A rancher.", rating="6.9"
            )
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert attempted_titles[0] == "McLintock!"
    assert enriched.title == "McLintock!"
    assert enriched.year == "1963"


def test_main_youtube_runs_tmdb_lookup_when_title_has_a_year(tmp_path, monkeypatch):

    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="Nosferatu (1922) Full Movie", author_name="Public Domain Archive", thumbnail_url="https://i.ytimg.com/y.jpg"),
    )

    captured_lookup = {}

    def fake_fetch(title, year, api_token, *args, **kwargs):
        captured_lookup.update(title=title, year=year, api_token=api_token)
        return MovieMetadata(
            title="Nosferatu",
            year="1922",
            poster_url="https://image.tmdb.org/t/p/w500/nosferatu.jpg",
            overview="A vampire's arrival brings terror.",
            rating="7.8",
        )

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert captured_lookup == {"title": "Nosferatu", "year": "1922", "api_token": "secret-token"}
    assert enriched.title == "Nosferatu"
    assert enriched.year == "1922"
    assert enriched.rating == "7.8"
    assert enriched.rating_is_tmdb is True
    assert enriched.description == "A vampire's arrival brings terror."
    assert enriched.poster_url == "https://image.tmdb.org/t/p/w500/nosferatu.jpg"


def test_main_youtube_tmdb_lookup_tries_the_split_title_before_the_full_remainder(tmp_path, monkeypatch):
    # Regression test for a real archive-channel upload (confirmed live
    # against youtube.com/watch?v=wEx-z1TYPKU): naively searching TMDB
    # with the whole year-stripped title ("His Girl Friday - Cary Grant
    # and Rosalind Russell - Ex-lovers become headline hunters") finds
    # nothing, so the split first-segment candidate must be tried first.
    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(
            title="1940 - His Girl Friday - Cary Grant and Rosalind Russell - Ex-lovers become headline hunters",
            author_name="Cult Cinema Classics",
            thumbnail_url="https://i.ytimg.com/hgf.jpg",
        ),
    )

    attempted_titles = []

    def fake_fetch(title, year, api_token, *args, **kwargs):
        attempted_titles.append(title)
        if title == "His Girl Friday":
            return MovieMetadata(
                title="His Girl Friday", year="1940", poster_url=None, overview="A screwball comedy.", rating="7.9"
            )
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert attempted_titles == ["His Girl Friday"]  # matched on the first (split) candidate -- no second attempt
    assert enriched.title == "His Girl Friday"
    assert enriched.rating == "7.9"


def test_main_youtube_tmdb_lookup_falls_back_to_the_full_remainder_when_the_split_candidate_misses(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(
            title="1965 - Alphaville - A Strange Adventure of Lemmy Caution",
            author_name="Cult Cinema Classics",
            thumbnail_url=None,
        ),
    )

    attempted_titles = []

    def fake_fetch(title, year, api_token, *args, **kwargs):
        attempted_titles.append(title)
        if title == "Alphaville - A Strange Adventure of Lemmy Caution":
            return MovieMetadata(title="Alphaville", year="1965", poster_url=None, overview=None, rating="7.6")
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert attempted_titles == ["Alphaville", "Alphaville - A Strange Adventure of Lemmy Caution"]
    assert enriched.title == "Alphaville"
    assert enriched.rating == "7.6"


def test_main_youtube_title_year_override_forces_tmdb_lookup_even_without_a_detected_year(tmp_path, monkeypatch):

    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="Some Ambiguous Upload Title", author_name=None, thumbnail_url=None),
    )

    captured_lookup = {}

    def fake_fetch(title, year, api_token, *args, **kwargs):
        captured_lookup.update(title=title, year=year, api_token=api_token)
        return MovieMetadata(title="Real Movie Title", year="2001", poster_url=None, overview=None, rating="6.5")

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(
        _youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token", "--title", "Real Movie Title", "--year", "2001")
    )

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert captured_lookup == {"title": "Real Movie Title", "year": "2001", "api_token": "secret-token"}
    assert enriched.title == "Real Movie Title"
    assert enriched.rating == "6.5"


def test_main_youtube_poster_falls_back_to_oembed_thumbnail_when_tmdb_has_none(tmp_path, monkeypatch):

    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="Some Movie (1999)", author_name=None, thumbnail_url="https://i.ytimg.com/z.jpg"),
    )
    monkeypatch.setattr(
        "tvdinner.cli.fetch_movie_metadata_cached",
        lambda *a, **k: MovieMetadata(title="Some Movie", year="1999", poster_url=None, overview=None, rating=None),
    )

    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "secret-token"))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert enriched.poster_url == "https://i.ytimg.com/z.jpg"


def test_main_falls_back_to_a_stored_tmdb_token_when_not_given_directly(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_TOKEN_PATH", tmp_path / "tmdb_token.json")
    (tmp_path / "tmdb_token.json").write_text('{"tmdb_api_token": "stored-token"}')

    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="Some Movie (1999)", author_name=None, thumbnail_url=None),
    )
    captured_token = {}

    def fake_fetch(title, year, api_token, *a, **k):
        captured_token["token"] = api_token
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)
    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    # No --tmdb-api-token given at all -- the stored default should be used.
    exit_code = main(_youtube_main_argv(tmp_path))

    assert exit_code == 0
    played["vod_metadata_loader"]()
    assert captured_token["token"] == "stored-token"


def test_main_explicit_tmdb_api_token_overrides_the_stored_one(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_TOKEN_PATH", tmp_path / "tmdb_token.json")
    (tmp_path / "tmdb_token.json").write_text('{"tmdb_api_token": "stored-token"}')

    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="Some Movie (1999)", author_name=None, thumbnail_url=None),
    )
    captured_token = {}

    def fake_fetch(title, year, api_token, *a, **k):
        captured_token["token"] = api_token
        return None

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fake_fetch)
    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path, "--tmdb-api-token", "explicit-token"))

    assert exit_code == 0
    played["vod_metadata_loader"]()
    assert captured_token["token"] == "explicit-token"


def test_main_no_tmdb_token_anywhere_means_no_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_TOKEN_PATH", tmp_path / "does-not-exist.json")

    monkeypatch.setattr(
        "tvdinner.cli.fetch_youtube_oembed",
        lambda url: YoutubeInfo(title="Some Movie (1999)", author_name=None, thumbnail_url=None),
    )

    def fail_fetch(*a, **k):
        raise AssertionError("should not query TMDB with no token from any source")

    monkeypatch.setattr("tvdinner.cli.fetch_movie_metadata_cached", fail_fetch)
    played = {}
    monkeypatch.setattr("tvdinner.cli.play_stream", lambda url, **kwargs: played.update(**kwargs) or 0)

    exit_code = main(_youtube_main_argv(tmp_path))

    assert exit_code == 0
    enriched = played["vod_metadata_loader"]()
    assert enriched.title == "Some Movie (1999)"  # unchanged -- TMDB never consulted


def test_format_cache_bytes_steps_through_units():
    assert _format_cache_bytes(0) == "0 B"
    assert _format_cache_bytes(512) == "512 B"
    assert _format_cache_bytes(2048) == "2.0 KB"
    assert _format_cache_bytes(5 * 1024 * 1024) == "5.0 MB"
    assert _format_cache_bytes(3 * 1024 * 1024 * 1024) == "3.0 GB"


def test_dir_size_returns_zero_for_a_missing_directory(tmp_path):
    assert _dir_size(tmp_path / "does-not-exist") == 0


def test_dir_size_sums_files_recursively(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.txt").write_bytes(b"y" * 250)
    assert _dir_size(tmp_path) == 350


def test_print_stats_table_aligns_columns_and_right_aligns_size(capsys):
    _print_stats_table(["Feed", "Size"], [["A", "1 KB"], ["Longer Name", "2.0 MB"]], right_align={1})
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("Feed")
    assert lines[2].endswith("1 KB")  # right-aligned within the Size column
    assert lines[3].endswith("2.0 MB")


def _write_bookmarks(path, bookmarks):
    path.write_text(json.dumps([{"name": b.name, "url": b.url, "epg": b.epg, "channel": b.channel, "tmdb_api_token": b.tmdb_api_token} for b in bookmarks]))


def test_run_stats_command_reports_no_bookmarks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")

    exit_code = run_stats_command(["--bookmarks-file", str(tmp_path / "no-bookmarks.json"), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No bookmarks saved" in out
    assert "Total" in out


def test_run_stats_command_sizes_a_bookmark_with_an_explicit_epg_url(tmp_path, monkeypatch, capsys):
    epg_dir = tmp_path / "epg"
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", epg_dir)
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")

    epg_url = "https://example.com/guide.xml"
    epg_dir.mkdir(parents=True)
    cache_path_for(epg_dir, epg_url, suffix=".xml").write_bytes(b"x" * 1000)
    parsed_cache_path_for(epg_dir, epg_url).write_bytes(b"y" * 24)

    bookmarks_path = tmp_path / "bookmarks.json"
    _write_bookmarks(
        bookmarks_path, [Bookmark(name="My Feed", url="https://example.com/playlist.m3u", epg=epg_url)]
    )

    exit_code = run_stats_command(["--bookmarks-file", str(bookmarks_path), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "My Feed" in out
    assert "1.0 KB" in out  # 1000 + 24 bytes


def test_run_stats_command_derives_xtream_epg_url_when_no_override(tmp_path, monkeypatch, capsys):
    epg_dir = tmp_path / "epg"
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", epg_dir)
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")

    creds = XtreamCreds(base_url="http://panel.example.com:8080", username="demo", password="demo", output="ts")
    epg_dir.mkdir(parents=True)
    cache_path_for(epg_dir, xtream_epg_url(creds), suffix=".xml").write_bytes(b"x" * 2048)

    bookmarks_path = tmp_path / "bookmarks.json"
    _write_bookmarks(
        bookmarks_path, [Bookmark(name="My Panel", url="xtream://demo:demo@panel.example.com:8080")]
    )

    exit_code = run_stats_command(["--bookmarks-file", str(bookmarks_path), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "My Panel" in out
    assert "2.0 KB" in out


def test_run_stats_command_marks_bare_m3u_bookmark_as_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")

    bookmarks_path = tmp_path / "bookmarks.json"
    _write_bookmarks(bookmarks_path, [Bookmark(name="Bare Playlist", url="https://example.com/playlist.m3u")])

    exit_code = run_stats_command(["--bookmarks-file", str(bookmarks_path), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Bare Playlist" in out
    assert "unknown" in out


def test_run_stats_command_reports_shared_cache_totals(tmp_path, monkeypatch, capsys):
    tmdb_dir = tmp_path / "tmdb"
    image_dir = tmp_path / "images"
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmdb_dir)
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", image_dir)
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")

    tmdb_dir.mkdir(parents=True)
    (tmdb_dir / "rating.json").write_bytes(b"x" * 1500)
    image_dir.mkdir(parents=True)
    (image_dir / "logo.img").write_bytes(b"y" * 3000)

    exit_code = run_stats_command(["--bookmarks-file", str(tmp_path / "no-bookmarks.json"), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "1.5 KB" in out  # TMDB total
    assert "2.9 KB" in out  # image cache total


def test_run_stats_command_excludes_online_logo_database_from_other_epg_bucket(tmp_path, monkeypatch, capsys):
    epg_dir = tmp_path / "epg"
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", epg_dir)
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")

    epg_dir.mkdir(parents=True)
    cache_path_for(epg_dir, CHANNELS_URL, suffix=".json").write_bytes(b"c" * 4000)
    cache_path_for(epg_dir, LOGOS_URL, suffix=".json").write_bytes(b"l" * 1000)

    exit_code = run_stats_command(["--bookmarks-file", str(tmp_path / "no-bookmarks.json"), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "4.9 KB" in out  # online channel/logo database total (4000 + 1000 bytes)
    assert "Other EPG cache (unbookmarked feeds)" in out
    # The online-logo-database bytes must not double-count into "other".
    assert "0 B" in out


def test_run_stats_command_reports_log_file_path_and_size(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")

    log_path = tmp_path / "tvdinner.log"
    log_path.write_bytes(b"z" * 2500)

    exit_code = run_stats_command(
        ["--bookmarks-file", str(tmp_path / "no-bookmarks.json"), "--log-file", str(log_path), "--no-log"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Log file" in out
    assert "2.4 KB" in out
    assert str(log_path) in out


def test_run_stats_command_reports_zero_size_for_missing_log_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "does-not-exist.log")

    exit_code = run_stats_command(["--bookmarks-file", str(tmp_path / "no-bookmarks.json"), "--no-log"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Log file" in out
    assert str(tmp_path / "does-not-exist.log") in out


def test_run_stats_command_includes_rotated_log_backup_in_size(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")

    log_path = tmp_path / "tvdinner.log"
    log_path.write_bytes(b"z" * 1500)
    (tmp_path / "tvdinner.log.1").write_bytes(b"z" * 1000)

    exit_code = run_stats_command(
        ["--bookmarks-file", str(tmp_path / "no-bookmarks.json"), "--log-file", str(log_path), "--no-log"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "2.4 KB" in out  # 1500 + 1000 bytes, live file plus rotated backup


def test_format_stats_duration():
    assert _format_stats_duration(30) == "30s"
    assert _format_stats_duration(90) == "1m"
    assert _format_stats_duration(3600) == "1h"
    assert _format_stats_duration(3660) == "1h 1m"


def test_period_starts_week_and_month_boundaries():
    # A Wednesday, mid-month -- week_start should land on the Monday of
    # the same week, month_start on the 1st of the same month, both at
    # local midnight; "All time" has no lower bound.
    now = datetime(2026, 8, 26, 15, 30, tzinfo=timezone.utc)
    periods = _period_starts(now)
    assert periods["This week"] == datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)
    assert periods["This month"] == datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    assert periods["All time"] is None


def _entry(kind, title, channel_name, started_at, duration_minutes):
    return HistoryEntry(
        kind=kind,
        title=title,
        url="http://example.com/stream",
        playlist_source=None,
        started_at=started_at,
        ended_at=started_at + timedelta(minutes=duration_minutes),
        channel_name=channel_name,
    )


def test_watch_seconds_by_kind_buckets_by_start_time_and_since():
    since = datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)
    entries = [
        _entry("channel", "EastEnders", "BBC One", datetime(2026, 8, 25, 20, 0, tzinfo=timezone.utc), 30),
        _entry("vod", "Big Buck Bunny", None, datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc), 90),
        # Before `since` -- excluded.
        _entry("channel", "News", "ITV", datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc), 20),
    ]
    totals = _watch_seconds_by_kind(entries, since)
    assert totals == {"channel": 30 * 60, "vod": 90 * 60, "recording": 0.0}


def test_watch_seconds_by_kind_none_since_includes_everything():
    entries = [
        _entry("recording", "My Recording", None, datetime(2000, 1, 1, tzinfo=timezone.utc), 60),
    ]
    totals = _watch_seconds_by_kind(entries, None)
    assert totals["recording"] == 60 * 60


def test_top_channels_ranks_by_total_duration_descending():
    entries = [
        _entry("channel", "EastEnders", "BBC One", datetime(2026, 8, 25, tzinfo=timezone.utc), 30),
        _entry("channel", "News at Ten", "BBC One", datetime(2026, 8, 25, tzinfo=timezone.utc), 15),
        _entry("channel", "Coronation Street", "ITV", datetime(2026, 8, 25, tzinfo=timezone.utc), 20),
        _entry("vod", "Big Buck Bunny", None, datetime(2026, 8, 25, tzinfo=timezone.utc), 999),
    ]
    top = _top_channels(entries, None)
    assert top == [("BBC One", 45 * 60), ("ITV", 20 * 60)]


def test_top_channels_respects_since_and_limit():
    since = datetime(2026, 8, 24, tzinfo=timezone.utc)
    entries = [
        _entry("channel", "A", "Channel A", datetime(2026, 8, 20, tzinfo=timezone.utc), 100),  # too old
        _entry("channel", "B", "Channel B", datetime(2026, 8, 25, tzinfo=timezone.utc), 10),
        _entry("channel", "C", "Channel C", datetime(2026, 8, 25, tzinfo=timezone.utc), 20),
    ]
    top = _top_channels(entries, since, limit=1)
    assert top == [("Channel C", 20 * 60)]


def test_top_channels_falls_back_to_title_without_a_channel_name():
    entries = [_entry("channel", "Fallback Channel", None, datetime(2026, 8, 25, tzinfo=timezone.utc), 10)]
    assert _top_channels(entries, None) == [("Fallback Channel", 10 * 60)]


def _stats_argv(tmp_path, history_path, *extra):
    return [
        "--bookmarks-file",
        str(tmp_path / "no-bookmarks.json"),
        "--history-file",
        str(history_path),
        "--no-log",
        *extra,
    ]


def _patch_stats_cache_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_LOG_PATH", tmp_path / "tvdinner.log")


def test_run_stats_command_reports_no_watch_history(tmp_path, monkeypatch, capsys):
    _patch_stats_cache_dirs(monkeypatch, tmp_path)

    exit_code = run_stats_command(_stats_argv(tmp_path, tmp_path / "history.jsonl"))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No watch history recorded yet." in out
    assert "Top channels" not in out


def test_run_stats_command_reports_watch_time_by_period_and_kind(tmp_path, monkeypatch, capsys):
    _patch_stats_cache_dirs(monkeypatch, tmp_path)
    history_path = tmp_path / "history.jsonl"
    now = datetime.now(timezone.utc)
    append_history_entry(
        history_path,
        HistoryEntry(
            kind="channel",
            title="EastEnders",
            url="http://example.com/stream",
            playlist_source=None,
            started_at=now - timedelta(hours=1, minutes=30),
            ended_at=now - timedelta(hours=1),
            channel_name="BBC One",
        ),
    )

    exit_code = run_stats_command(_stats_argv(tmp_path, history_path))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Watching activity" in out
    assert "This week" in out
    assert "This month" in out
    assert "All time" in out
    assert "30m" in out  # the one channel watch, in every period it falls into
    assert "BBC One" in out  # shows up in the top-channels tables too


def test_run_stats_command_skips_top_channels_for_vod_only_history(tmp_path, monkeypatch, capsys):
    _patch_stats_cache_dirs(monkeypatch, tmp_path)
    history_path = tmp_path / "history.jsonl"
    now = datetime.now(timezone.utc)
    append_history_entry(
        history_path,
        HistoryEntry(
            kind="vod",
            title="Big Buck Bunny",
            url="http://example.com/movie.mp4",
            playlist_source=None,
            started_at=now - timedelta(minutes=90),
            ended_at=now,
        ),
    )

    exit_code = run_stats_command(_stats_argv(tmp_path, history_path))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Watching activity" in out
    assert "Top channels" not in out


def _patch_hard_reset_global_paths(monkeypatch, tmp_path):
    # run_hard_reset_command touches DEFAULT_EPG_CACHE_DIR/
    # DEFAULT_TMDB_CACHE_DIR/DEFAULT_IMAGE_CACHE_DIR/DEFAULT_UPDATE_CHECK_PATH
    # unconditionally -- none of them have a CLI override flag anywhere in
    # the app (see cli.py's stats command, which has the same limitation)
    # -- so every test of this command must redirect all four away from
    # the real ones, or it would delete the real machine's actual tvdinner
    # state.
    #
    # DEFAULT_HISTORY_PATH/DEFAULT_TMDB_TOKEN_PATH *do* have CLI overrides
    # (--history-file/--tmdb-token-file, always passed by
    # _hard_reset_argv below) -- these two are patched anyway, as a
    # second line of defense. Confirmed live via a real auditd watch
    # (2026-08-15) that a test omitting one of those flags -- two tests
    # here used to build their own argv by hand and simply forgot
    # --tmdb-token-file -- silently deleted the developer's actual
    # ~/.config/tvdinner/tmdb_token.json and history.jsonl on every
    # `pytest` run, for hours, before being caught. Never rely on "every
    # call site remembers the flag" alone again for a path this
    # destructive.
    monkeypatch.setattr("tvdinner.cli.DEFAULT_EPG_CACHE_DIR", tmp_path / "cache" / "epg")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_CACHE_DIR", tmp_path / "cache" / "tmdb")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_IMAGE_CACHE_DIR", tmp_path / "cache" / "images")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_UPDATE_CHECK_PATH", tmp_path / "config" / "update_check.json")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_HISTORY_PATH", tmp_path / "config" / "history.jsonl")
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_TOKEN_PATH", tmp_path / "config" / "tmdb_token.json")


def _hard_reset_argv(tmp_path, *extra):
    return [
        "--bookmarks-file",
        str(tmp_path / "bookmarks.json"),
        "--favorites",
        str(tmp_path / "favorites.json"),
        "--epg-shifts",
        str(tmp_path / "epg_shifts.json"),
        "--tmdb-token-file",
        str(tmp_path / "tmdb_token.json"),
        "--schedule-file",
        str(tmp_path / "schedule.json"),
        "--playback-positions-file",
        str(tmp_path / "positions.json"),
        "--history-file",
        str(tmp_path / "history.jsonl"),
        "--no-log",
        *extra,
    ]


def test_run_hard_reset_command_cancels_without_deleting_when_declined(tmp_path, monkeypatch, capsys):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    bookmarks_path = tmp_path / "bookmarks.json"
    bookmarks_path.write_text("{}")
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    exit_code = run_hard_reset_command(_hard_reset_argv(tmp_path))

    assert exit_code == 0
    assert bookmarks_path.is_file()
    out = capsys.readouterr().out
    assert "cancelled" in out.lower()


def test_run_hard_reset_command_never_prompts_with_yes_flag(tmp_path, monkeypatch):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)

    def fail_input(prompt):
        raise AssertionError("should not prompt when -y is given")

    monkeypatch.setattr("builtins.input", fail_input)

    exit_code = run_hard_reset_command(_hard_reset_argv(tmp_path, "-y"))

    assert exit_code == 0


def test_run_hard_reset_command_removes_config_files_and_cache_dirs(tmp_path, monkeypatch, capsys):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)

    config_files = ["bookmarks.json", "favorites.json", "epg_shifts.json", "schedule.json", "positions.json"]
    for name in config_files:
        (tmp_path / name).write_text("{}")

    (tmp_path / "cache" / "epg").mkdir(parents=True)
    (tmp_path / "cache" / "epg" / "somehash.xml").write_bytes(b"x" * 100)
    (tmp_path / "cache" / "tmdb").mkdir(parents=True)
    (tmp_path / "cache" / "tmdb" / "rating.json").write_bytes(b"y" * 50)
    (tmp_path / "cache" / "images").mkdir(parents=True)
    (tmp_path / "cache" / "images" / "logo.img").write_bytes(b"z" * 50)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "update_check.json").write_text("{}")

    exit_code = run_hard_reset_command(_hard_reset_argv(tmp_path, "-y"))

    assert exit_code == 0
    for name in config_files:
        assert not (tmp_path / name).exists()
    assert not (tmp_path / "cache" / "epg").exists()
    assert not (tmp_path / "cache" / "tmdb").exists()
    assert not (tmp_path / "cache" / "images").exists()
    assert not (tmp_path / "config" / "update_check.json").exists()

    out = capsys.readouterr().out
    assert "Removed 9 item(s)" in out


def test_run_hard_reset_command_never_touches_recordings(tmp_path, monkeypatch):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    recording = recordings_dir / "Some Show_20260101-120000.ts"
    recording.write_bytes(b"totally real recording")

    exit_code = run_hard_reset_command(_hard_reset_argv(tmp_path, "-y"))

    assert exit_code == 0
    assert recording.is_file()
    assert recording.read_bytes() == b"totally real recording"


def test_run_hard_reset_command_mentions_recordings_are_preserved_in_the_prompt(tmp_path, monkeypatch, capsys):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    run_hard_reset_command(_hard_reset_argv(tmp_path))

    out = capsys.readouterr().out
    assert "Recordings" in out
    assert "never touched" in out


def test_run_hard_reset_command_tolerates_files_that_do_not_exist(tmp_path, monkeypatch, capsys):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    # Nothing created at all -- a fresh install running this should not error.

    exit_code = run_hard_reset_command(_hard_reset_argv(tmp_path, "-y"))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Nothing to remove" in out


def test_run_hard_reset_command_closes_and_removes_its_own_log_file(tmp_path, monkeypatch):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    log_path = tmp_path / "log" / "tvdinner.log"

    exit_code = run_hard_reset_command(
        [
            "--bookmarks-file",
            str(tmp_path / "bookmarks.json"),
            "--favorites",
            str(tmp_path / "favorites.json"),
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--tmdb-token-file",
            str(tmp_path / "tmdb_token.json"),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "positions.json"),
            "--history-file",
            str(tmp_path / "history.jsonl"),
            "--log-file",
            str(log_path),
            "-y",
        ]
    )

    assert exit_code == 0
    assert not log_path.exists()


def test_run_hard_reset_command_removes_rotated_log_backup(tmp_path, monkeypatch):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    log_path = log_dir / "tvdinner.log"
    log_path.write_bytes(b"current")
    backup_path = log_dir / "tvdinner.log.1"
    backup_path.write_bytes(b"rotated")

    exit_code = run_hard_reset_command(
        [
            "--bookmarks-file",
            str(tmp_path / "bookmarks.json"),
            "--favorites",
            str(tmp_path / "favorites.json"),
            "--epg-shifts",
            str(tmp_path / "epg_shifts.json"),
            "--tmdb-token-file",
            str(tmp_path / "tmdb_token.json"),
            "--schedule-file",
            str(tmp_path / "schedule.json"),
            "--playback-positions-file",
            str(tmp_path / "positions.json"),
            "--history-file",
            str(tmp_path / "history.jsonl"),
            "--log-file",
            str(log_path),
            "-y",
        ]
    )

    assert exit_code == 0
    assert not log_path.exists()
    assert not backup_path.exists()


def test_run_hard_reset_command_removes_stored_tmdb_token(tmp_path, monkeypatch):
    _patch_hard_reset_global_paths(monkeypatch, tmp_path)
    token_path = tmp_path / "tmdb_token.json"
    token_path.write_text('{"tmdb_api_token": "secret-token"}')

    exit_code = run_hard_reset_command(_hard_reset_argv(tmp_path, "-y"))

    assert exit_code == 0
    assert not token_path.exists()


def _store_tmdb_argv(tmp_path, token, *extra):
    return [token, "--tmdb-token-file", str(tmp_path / "tmdb_token.json"), "--no-log", *extra]


def test_run_store_tmdb_command_saves_the_token(tmp_path, capsys):
    token_path = tmp_path / "tmdb_token.json"

    exit_code = run_store_tmdb_command(_store_tmdb_argv(tmp_path, "secret-token"))

    assert exit_code == 0
    assert json.loads(token_path.read_text()) == {"tmdb_api_token": "secret-token"}
    out = capsys.readouterr().out
    assert "saved" in out.lower()


def test_run_store_tmdb_command_overwrites_a_previous_token(tmp_path):
    token_path = tmp_path / "tmdb_token.json"
    run_store_tmdb_command(_store_tmdb_argv(tmp_path, "old-token"))
    run_store_tmdb_command(_store_tmdb_argv(tmp_path, "new-token"))

    assert json.loads(token_path.read_text()) == {"tmdb_api_token": "new-token"}


def test_run_store_tmdb_command_never_logs_the_token_itself(tmp_path):
    log_path = tmp_path / "tvdinner.log"

    exit_code = run_store_tmdb_command(
        ["super-secret-value", "--tmdb-token-file", str(tmp_path / "tmdb_token.json"), "--log-file", str(log_path)]
    )

    assert exit_code == 0
    assert "super-secret-value" not in log_path.read_text()


def test_run_store_tmdb_command_uses_the_default_path_without_an_override(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.cli.DEFAULT_TMDB_TOKEN_PATH", tmp_path / "default" / "tmdb_token.json")

    exit_code = run_store_tmdb_command(["secret-token", "--no-log"])

    assert exit_code == 0
    assert json.loads((tmp_path / "default" / "tmdb_token.json").read_text()) == {"tmdb_api_token": "secret-token"}


def test_run_clear_tmdb_command_removes_an_existing_token(tmp_path, capsys):
    token_path = tmp_path / "tmdb_token.json"
    token_path.write_text('{"tmdb_api_token": "secret-token"}')

    exit_code = run_clear_tmdb_command(["--tmdb-token-file", str(token_path), "--no-log"])

    assert exit_code == 0
    assert not token_path.exists()
    out = capsys.readouterr().out
    assert "Removed" in out


def test_run_clear_tmdb_command_reports_nothing_to_remove(tmp_path, capsys):
    exit_code = run_clear_tmdb_command(
        ["--tmdb-token-file", str(tmp_path / "does-not-exist.json"), "--no-log"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No stored TMDB token" in out


def _gdrive_login_argv(tmp_path, *extra):
    return ["--gdrive-token-file", str(tmp_path / "gdrive_token.json"), "--no-log", *extra]


def test_run_gdrive_login_command_saves_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "tvdinner.cli.gdrive_login",
        lambda client_id, client_secret, open_browser=True: {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": "new-refresh-token",
        },
    )

    exit_code = run_gdrive_login_command(
        _gdrive_login_argv(tmp_path, "--client-id", "cid", "--client-secret", "csecret")
    )

    assert exit_code == 0
    saved = json.loads((tmp_path / "gdrive_token.json").read_text())
    assert saved == {"client_id": "cid", "client_secret": "csecret", "refresh_token": "new-refresh-token"}
    assert "Signed in" in capsys.readouterr().out


def test_run_gdrive_login_command_reuses_stored_client_id_when_omitted(tmp_path, monkeypatch):
    token_path = tmp_path / "gdrive_token.json"
    token_path.write_text(
        json.dumps({"client_id": "stored-cid", "client_secret": "stored-secret", "refresh_token": "old-refresh"})
    )
    seen = {}

    def fake_login(client_id, client_secret, open_browser=True):
        seen["client_id"] = client_id
        seen["client_secret"] = client_secret
        return {"client_id": client_id, "client_secret": client_secret, "refresh_token": "new-refresh"}

    monkeypatch.setattr("tvdinner.cli.gdrive_login", fake_login)

    exit_code = run_gdrive_login_command(_gdrive_login_argv(tmp_path))

    assert exit_code == 0
    assert seen == {"client_id": "stored-cid", "client_secret": "stored-secret"}


def test_run_gdrive_login_command_falls_back_to_the_bundled_client_when_none_given(tmp_path, monkeypatch):
    seen = {}

    def fake_login(client_id, client_secret, open_browser=True):
        seen["client_id"] = client_id
        seen["client_secret"] = client_secret
        return {"client_id": client_id, "client_secret": client_secret, "refresh_token": "new-refresh"}

    monkeypatch.setattr("tvdinner.cli.gdrive_login", fake_login)

    exit_code = run_gdrive_login_command(_gdrive_login_argv(tmp_path))

    assert exit_code == 0
    assert seen == {"client_id": BUNDLED_CLIENT_ID, "client_secret": BUNDLED_CLIENT_SECRET}


def test_run_gdrive_login_command_reports_gdrive_error(tmp_path, monkeypatch, capsys):
    def fake_login(client_id, client_secret, open_browser=True):
        raise GdriveError("sign-in failed")

    monkeypatch.setattr("tvdinner.cli.gdrive_login", fake_login)

    exit_code = run_gdrive_login_command(
        _gdrive_login_argv(tmp_path, "--client-id", "cid", "--client-secret", "csecret")
    )

    assert exit_code == 1
    assert "sign-in failed" in capsys.readouterr().err


def test_run_gdrive_logout_command_removes_existing_credentials(tmp_path, capsys):
    token_path = tmp_path / "gdrive_token.json"
    token_path.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))

    exit_code = run_gdrive_logout_command(["--gdrive-token-file", str(token_path), "--no-log"])

    assert exit_code == 0
    assert not token_path.exists()
    assert "Removed" in capsys.readouterr().out


def test_run_gdrive_logout_command_reports_nothing_to_remove(tmp_path, capsys):
    exit_code = run_gdrive_logout_command(
        ["--gdrive-token-file", str(tmp_path / "does-not-exist.json"), "--no-log"]
    )

    assert exit_code == 0
    assert "No stored Google Drive sign-in" in capsys.readouterr().out


def _gdrive_credentials_file(tmp_path):
    path = tmp_path / "gdrive_token.json"
    path.write_text(json.dumps({"client_id": "a", "client_secret": "b", "refresh_token": "c"}))
    return path


def test_run_backup_command_gdrive_uploads_the_created_archive(tmp_path, monkeypatch):
    token_path = _gdrive_credentials_file(tmp_path)
    output_path = tmp_path / "out.zip"
    uploaded = {}

    def fake_upload(credentials, name, data):
        uploaded["credentials"] = credentials
        uploaded["name"] = name
        uploaded["data"] = data

    monkeypatch.setattr("tvdinner.cli.upload_gdrive_backup", fake_upload)

    exit_code = run_backup_command(
        [
            str(output_path),
            "--epg-shifts",
            str(tmp_path / "missing-epg-shifts.json"),
            "--favorites",
            str(tmp_path / "missing-favorites.json"),
            "--bookmarks-file",
            str(tmp_path / "missing-bookmarks.json"),
            "--tmdb-token-file",
            str(tmp_path / "missing-tmdb.json"),
            "--gdrive",
            "--gdrive-token-file",
            str(token_path),
            "--no-log",
        ]
    )

    assert exit_code == 0
    assert uploaded["credentials"] == {"client_id": "a", "client_secret": "b", "refresh_token": "c"}
    assert uploaded["name"] == "tvdinner-backup.zip"
    assert uploaded["data"] == output_path.read_bytes()


def test_run_backup_command_gdrive_without_login_fails_before_creating_archive(tmp_path, capsys):
    output_path = tmp_path / "out.zip"

    exit_code = run_backup_command(
        [
            str(output_path),
            "--gdrive",
            "--gdrive-token-file",
            str(tmp_path / "missing-token.json"),
            "--no-log",
        ]
    )

    assert exit_code == 1
    assert not output_path.exists()
    assert "Not signed in" in capsys.readouterr().err


def test_run_restore_command_gdrive_downloads_and_restores(tmp_path, monkeypatch):
    token_path = _gdrive_credentials_file(tmp_path)
    favorites_path = tmp_path / "favorites.json"
    downloaded = {}

    import zipfile
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("favorites.json", '{"restored": true}')

    def fake_download(credentials, name):
        downloaded["credentials"] = credentials
        downloaded["name"] = name
        return buffer.getvalue()

    monkeypatch.setattr("tvdinner.cli.download_gdrive_backup", fake_download)

    exit_code = run_restore_command(
        [
            "--epg-shifts",
            str(tmp_path / "missing-epg-shifts.json"),
            "--favorites",
            str(favorites_path),
            "--bookmarks-file",
            str(tmp_path / "missing-bookmarks.json"),
            "--tmdb-token-file",
            str(tmp_path / "missing-tmdb.json"),
            "--gdrive",
            "--gdrive-token-file",
            str(token_path),
            "-y",
            "--no-log",
        ]
    )

    assert exit_code == 0
    assert downloaded["credentials"] == {"client_id": "a", "client_secret": "b", "refresh_token": "c"}
    assert downloaded["name"] == "tvdinner-backup.zip"
    assert json.loads(favorites_path.read_text()) == {"restored": True}


def test_run_restore_command_requires_path_or_gdrive(capsys):
    with pytest.raises(SystemExit) as exc_info:
        run_restore_command(["--no-log"])

    assert exc_info.value.code == 2
    assert "PATH" in capsys.readouterr().err


def _plex_node(title="Movie", kind="movie", **kwargs) -> PlexNode:
    return PlexNode(rating_key=title, title=title, kind=kind, **kwargs)


def _unfiltered(frame: _PlexNavFrame) -> list[PlexNode]:
    """Stand-in for cli.py's own plex_frame_nodes, for tests that aren't
    exercising the favorites-only filtering itself."""
    return frame.nodes


def test_plex_title_logo_target_returns_the_selected_show_at_the_top_level():
    frame = _PlexNavFrame(breadcrumb="TV", nodes=[_plex_node("Breaking Bad", kind="show")], selected_index=0)
    target = _plex_title_logo_target([frame], _unfiltered)
    assert target is not None
    assert target.title == "Breaking Bad"


def test_plex_title_logo_target_returns_the_selected_movie():
    frame = _PlexNavFrame(breadcrumb="Movies", nodes=[_plex_node("The Matrix", kind="movie")], selected_index=0)
    target = _plex_title_logo_target([frame], _unfiltered)
    assert target is not None
    assert target.title == "The Matrix"


def test_plex_title_logo_target_walks_up_to_the_show_from_a_season_listing():
    show_frame = _PlexNavFrame(breadcrumb="TV", nodes=[_plex_node("Breaking Bad", kind="show")], selected_index=0)
    season_frame = _PlexNavFrame(breadcrumb="Breaking Bad", nodes=[_plex_node("Season 1", kind="season")], selected_index=0)
    target = _plex_title_logo_target([show_frame, season_frame], _unfiltered)
    assert target is not None
    assert target.title == "Breaking Bad"


def test_plex_title_logo_target_episode_uses_its_own_grandparent_rating_key():
    # An episode's own grandparent_rating_key (Plex's grandparentRatingKey,
    # present on the episode's own metadata regardless of listing
    # context) is used directly -- a real, usable rating_key, not just a
    # display title -- rather than walking the nav stack outward, no
    # matter what (if anything) sits in an outer frame.
    episode_frame = _PlexNavFrame(
        breadcrumb="Season 1",
        nodes=[_plex_node("Pilot", kind="episode", series_title="Breaking Bad", grandparent_rating_key="20")],
        selected_index=0,
    )
    target = _plex_title_logo_target([episode_frame], _unfiltered)
    assert target is not None
    assert target.title == "Breaking Bad"
    assert target.kind == "show"
    assert target.rating_key == "20"


def test_plex_title_logo_target_episode_ignores_an_unrelated_outer_frames_show():
    # Regression test for a real reported bug: searching the library and
    # selecting an episode result played the theme (and would have shown
    # the title logo) of whatever unrelated show was still selected in
    # the browsing session's own frame underneath the search-results
    # frame -- that frame is not this episode's ancestor just because it
    # happens to be sitting in the stack below it, unlike a real
    # show -> season -> episode drill-down. The episode's own
    # grandparent_rating_key must win regardless of what's underneath.
    unrelated_show_frame = _PlexNavFrame(breadcrumb="TV", nodes=[_plex_node("Better Call Saul", kind="show")], selected_index=0)
    search_results_frame = _PlexNavFrame(
        breadcrumb="Search: streets",
        nodes=[_plex_node("Coming Home", kind="episode", series_title="Streets of San Francisco", grandparent_rating_key="99")],
        selected_index=0,
    )
    target = _plex_title_logo_target([unrelated_show_frame, search_results_frame], _unfiltered)
    assert target is not None
    assert target.title == "Streets of San Francisco"
    assert target.rating_key == "99"


def test_plex_title_logo_target_falls_back_to_series_title_for_an_on_deck_episode():
    # Continue Watching's on-deck listing puts an episode directly under
    # a synthetic "continue_watching" container -- with no
    # grandparent_rating_key either (the rare item missing even that),
    # this falls back to a synthetic node built from the episode's own
    # series_title instead.
    root_frame = _PlexNavFrame(breadcrumb="Plex Libraries", nodes=[_plex_node("On Deck", kind="continue_watching")], selected_index=0)
    on_deck_frame = _PlexNavFrame(
        breadcrumb="On Deck",
        nodes=[_plex_node("Pilot", kind="episode", series_title="Breaking Bad", year="2019")],
        selected_index=0,
    )
    target = _plex_title_logo_target([root_frame, on_deck_frame], _unfiltered)
    assert target is not None
    assert target.title == "Breaking Bad"
    assert target.kind == "show"
    assert target.year == "2019"


def test_plex_title_logo_target_none_for_an_on_deck_episode_without_series_title():
    root_frame = _PlexNavFrame(breadcrumb="Plex Libraries", nodes=[_plex_node("On Deck", kind="continue_watching")], selected_index=0)
    on_deck_frame = _PlexNavFrame(breadcrumb="On Deck", nodes=[_plex_node("Pilot", kind="episode")], selected_index=0)
    assert _plex_title_logo_target([root_frame, on_deck_frame], _unfiltered) is None


def test_plex_title_logo_target_returns_the_movie_itself_for_an_on_deck_movie():
    root_frame = _PlexNavFrame(breadcrumb="Plex Libraries", nodes=[_plex_node("On Deck", kind="continue_watching")], selected_index=0)
    on_deck_frame = _PlexNavFrame(breadcrumb="On Deck", nodes=[_plex_node("The Matrix", kind="movie")], selected_index=0)
    target = _plex_title_logo_target([root_frame, on_deck_frame], _unfiltered)
    assert target is not None
    assert target.title == "The Matrix"


def test_plex_title_logo_target_none_for_a_library_listing_itself():
    frame = _PlexNavFrame(breadcrumb="Plex Libraries", nodes=[_plex_node("Movies", kind="library_movie")], selected_index=0)
    assert _plex_title_logo_target([frame], _unfiltered) is None


def test_plex_title_logo_target_shares_one_rating_key_per_show_name():
    root_frame = _PlexNavFrame(breadcrumb="Plex Libraries", nodes=[_plex_node("On Deck", kind="continue_watching")], selected_index=0)
    on_deck_frame_1 = _PlexNavFrame(
        breadcrumb="On Deck", nodes=[_plex_node("Pilot", kind="episode", series_title="Breaking Bad")], selected_index=0
    )
    on_deck_frame_2 = _PlexNavFrame(
        breadcrumb="On Deck", nodes=[_plex_node("Cat's in the Bag...", kind="episode", series_title="Breaking Bad")], selected_index=0
    )
    target_1 = _plex_title_logo_target([root_frame, on_deck_frame_1], _unfiltered)
    target_2 = _plex_title_logo_target([root_frame, on_deck_frame_2], _unfiltered)
    assert target_1 is not None and target_2 is not None
    assert target_1.rating_key == target_2.rating_key


def test_plex_title_logo_target_uses_frame_nodes_not_the_raw_unfiltered_list():
    # Regression test for a real reported bug: favoriting "The Green
    # Berets" (not the first item in the unfiltered library) and
    # switching to favorites-only correctly showed its backdrop
    # (render_and_show_plex already read the filtered list) but the
    # title logo still came from whatever the *unfiltered* list's
    # same-index item was -- selected_index indexes the filtered view
    # a caller's frame_nodes produces, not frame.nodes itself.
    frame = _PlexNavFrame(
        breadcrumb="Movies",
        nodes=[_plex_node("The Amorous Adventures of Moll Flanders"), _plex_node("The Green Berets")],
        selected_index=0,  # first (only) row of the *filtered* list below
    )

    def favorites_only(f: _PlexNavFrame) -> list[PlexNode]:
        return [n for n in f.nodes if n.title == "The Green Berets"]

    target = _plex_title_logo_target([frame], favorites_only)
    assert target is not None
    assert target.title == "The Green Berets"
