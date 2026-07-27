"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tvdinner import __version__
from tvdinner.backup import create_backup, restore_backup
from tvdinner.bookmarks import DEFAULT_BOOKMARKS_PATH
from tvdinner.bookmarks_tui import run_bookmarks_tui
from tvdinner.epg import (
    DEFAULT_CHANNEL_SHIFTS_PATH,
    DEFAULT_EPG_CACHE_DIR,
    Epg,
    EpgDisplay,
    Programme,
    format_time_shift,
    load_channel_shifts,
    load_epg_for_playlist,
    parse_time_shift,
    resolve_timezone,
    save_channel_shifts,
)
from tvdinner.favorites import DEFAULT_FAVORITES_PATH, load_favorites, save_favorites
from tvdinner.log import DEFAULT_LOG_PATH, configure_logging
from tvdinner.m3u import Channel, load_playlist
from tvdinner.overlay import (
    fetch_image,
    guide_eligible_channels,
    guide_reference_time,
    render_epg_overlay,
    render_guide_filter_prompt,
    render_help_overlay,
    render_program_guide,
    render_programme_details,
    render_recording_overlay,
    render_recordings_browser,
    render_schedule_browser,
    selected_guide_programme,
    visible_guide_channels,
    visible_recordings,
)
from tvdinner.player import (
    DEFAULT_LIVE_BUFFER_MINUTES,
    DEFAULT_RECORDINGS_DIR,
    Player,
    RecordingFile,
    StreamInfo,
    list_recordings,
    live_buffer_mpv_options,
)
from tvdinner.playback_positions import (
    DEFAULT_PLAYBACK_POSITIONS_PATH,
    load_playback_positions,
    save_playback_positions,
)
from tvdinner.schedule import DEFAULT_SCHEDULE_PATH, ScheduledRecording, load_schedule, save_schedule

logger = logging.getLogger(__name__)

_OVERLAY_TOP_MARGIN = 40
_GUIDE_BOTTOM_MARGIN = 40
_OVERLAY_HIDE_AFTER_SECONDS = 6.0
_OVERLAY_RESIZE_DEBOUNCE_SECONDS = 0.2
_OVERLAY_MOUSE_MOVE_THROTTLE_SECONDS = 1.0
_GUIDE_OVERLAY_ID = 1
_DETAILS_OVERLAY_ID = 2
_FILTER_OVERLAY_ID = 3
_RECORDINGS_OVERLAY_ID = 4
_SCHEDULE_OVERLAY_ID = 5
_HELP_OVERLAY_ID = 6
_GUIDE_TIME_STEP = timedelta(minutes=30)
_SHIFT_NUDGE_STEP = timedelta(minutes=1)
_GUIDE_MAX_ROWS = 8  # kept in sync with render_and_show_guide's max_rows so a page = a full screen
_RECORDINGS_MAX_ROWS = 8  # kept in sync with render_and_show_recordings's max_rows, like _GUIDE_MAX_ROWS
_SCHEDULE_MAX_ROWS = 8  # kept in sync with render_and_show_schedule's max_rows, like _GUIDE_MAX_ROWS
_MISSED_SCHEDULE_HISTORY_LIMIT = 10  # capped so a long session's conflicts don't grow the 'u' view unbounded
_RESUME_MIN_SECONDS = 10.0  # don't bother resuming a recording barely started
_RESUME_END_MARGIN_SECONDS = 15.0  # this close to the end counts as "fully watched" -- start over, don't resume
_PLAYBACK_POSITION_AUTOSAVE_SECONDS = 10.0  # periodic, not just on switch/quit -- mpv's core is already gone by the
# time the 'finally' block runs after the user quits via mpv's own default 'q', so that alone can't be relied on
# Keys with no meaning outside the guide; suspended while typing a filter
# query too, since they have no character-input equivalent to shadow them.
_GUIDE_NAV_ONLY_KEYS = ("LEFT", "RIGHT", "UP", "DOWN", "PGUP", "PGDWN", "[", "]")
_FILTER_INPUT_CHARS = list("abcdefghijklmnopqrstuvwxyz0123456789")
_DEFAULT_CANVAS_WIDTH = 1920
_DEFAULT_CANVAS_HEIGHT = 1080
_OSD_SIZE_WAIT_SECONDS = 2.0
_OSD_SIZE_POLL_INTERVAL = 0.05
_SCHEDULE_POLL_SECONDS = 15.0

# None = automatic (the container/stream's own aspect ratio); cycled with 'z'.
# 'stretch' fills the window exactly, distorting the image if needed -- see
# Player.set_video_aspect.
_ASPECT_RATIOS: list[tuple[str | None, str]] = [
    (None, "Auto"),
    ("4:3", "4:3"),
    ("16:9", "16:9"),
    ("2.35:1", "2.35:1 (Cinematic)"),
    ("1:1", "1:1"),
    ("stretch", "Stretch"),
]

_RECORDING_UNSAFE_CHARS = re.compile(r"[^\w\-. ]")


def recording_filename(label: str, now: datetime) -> str:
    """A filesystem-safe recording filename for `label` (a channel name or
    stream title/URL), timestamped so repeated recordings of the same
    channel don't collide. Always '.ts' regardless of the source stream's
    actual container -- stream-record is a raw byte copy, not a re-mux, and
    IPTV sources are overwhelmingly MPEG-TS already."""
    safe_label = _RECORDING_UNSAFE_CHARS.sub("_", label).strip("_ ") or "stream"
    return f"{safe_label}_{now.strftime('%Y%m%d-%H%M%S')}.ts"


def schedule_window(entry: ScheduledRecording, display: EpgDisplay) -> tuple[datetime, datetime]:
    """The real, absolute start/stop moments for a scheduled recording,
    directly comparable to datetime.now(timezone.utc) -- entry.start/stop
    are raw feed times (same as a Programme's), which need this channel's
    clock-shift correction (EpgDisplay.shift_for) applied before they mean
    anything on the real timeline. Comparing them unshifted was a real bug:
    a channel with a non-zero --epg-shifts entry (or --time-shift default)
    would schedule-check against the wrong wall-clock time -- e.g.
    reporting a programme as 'already ended' when it hadn't started yet,
    or actually starting/stopping a recording hours off from when the
    channel's guide says it airs."""
    shift = display.shift_for(entry.channel_name)
    return entry.start + shift, entry.stop + shift


def _resolve_canvas_width(player: Player) -> int:
    """The real window/OSD width, waited for briefly so the very first
    overlay (shown right after playback starts, before mpv has decoded a
    frame) isn't sized against a guess -- which previously made it look
    oversized compared to the correctly-sized overlay shown on a later 'i'
    press."""
    deadline = time.monotonic() + _OSD_SIZE_WAIT_SECONDS
    while time.monotonic() < deadline:
        osd_size = player.osd_size()
        if osd_size:
            return osd_size[0]
        time.sleep(_OSD_SIZE_POLL_INTERVAL)
    osd_size = player.osd_size()
    return osd_size[0] if osd_size else _DEFAULT_CANVAS_WIDTH


def current_and_next_programmes(
    channel: Channel, epg: Epg | None, display: EpgDisplay | None, now: datetime
) -> tuple[Programme | None, Programme | None]:
    if epg is None or display is None:
        return None, None
    return display.now_and_next(
        epg, channel.tvg_id, now, channel_name=channel.name, match_name=channel.tvg_name or channel.name
    )


def now_and_next_text(
    channel: Channel, epg: Epg | None, display: EpgDisplay | None, now: datetime
) -> tuple[str | None, str | None]:
    """Format the current and upcoming programme for a channel as
    ('Now: ...', 'Next: ...') strings, whichever are available."""
    current, upcoming = current_and_next_programmes(channel, epg, display, now)
    now_text = None
    next_text = None
    if current:
        start = display.to_local(current.start, channel_name=channel.name).strftime("%H:%M")
        stop = display.to_local(current.stop, channel_name=channel.name).strftime("%H:%M")
        now_text = f"Now: {current.title} ({start}–{stop})"
    if upcoming:
        start = display.to_local(upcoming.start, channel_name=channel.name).strftime("%H:%M")
        next_text = f"Next: {upcoming.title} ({start})"
    return now_text, next_text


def stream_quality_badges(info: StreamInfo | None) -> list[str]:
    """Convert a Player.stream_info() snapshot into the small ordered list
    of display-ready badge strings render_epg_overlay draws under the
    channel name, e.g. ['1080p', 'H.264', '29.97fps', 'AAC', 'Stereo'].
    Any field mpv hasn't probed yet (or the stream doesn't have) is simply
    omitted rather than shown as a placeholder."""
    if info is None:
        return []
    return [b for b in (info.resolution, info.video_codec, info.fps, info.hdr, info.audio_codec, info.audio_channels) if b]


def format_channel_line(
    index: int,
    channel: Channel,
    width: int,
    epg: Epg | None,
    display: EpgDisplay | None,
    now: datetime,
) -> str:
    group = f" [{channel.group_title}]" if channel.group_title else ""
    line = f"{index:>{width}}. {channel.name}{group}"

    now_text, next_text = now_and_next_text(channel, epg, display, now)
    parts = [part for part in (now_text, next_text) if part]
    if parts:
        line += "  " + " · ".join(parts)

    return line


def print_channel_list(
    channels: list[Channel],
    epg: Epg | None = None,
    display: EpgDisplay | None = None,
    file=sys.stdout,
) -> None:
    width = len(str(len(channels)))
    now = datetime.now(timezone.utc)
    for index, channel in enumerate(channels, start=1):
        print(format_channel_line(index, channel, width, epg, display, now), file=file)


def select_channel(channels: list[Channel], selector: str) -> Channel | None:
    """Resolve a 1-based index or a channel name (case-insensitive, exact
    then substring match) to a Channel."""
    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(channels):
            return channels[index - 1]
        return None

    lowered = selector.lower()
    for channel in channels:
        if channel.name.lower() == lowered:
            return channel
    matches = [c for c in channels if lowered in c.name.lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def play_stream(
    url: str,
    title: str | None = None,
    channel: Channel | None = None,
    channels: list[Channel] | None = None,
    epg: Epg | None = None,
    epg_loader: Callable[[], Epg | None] | None = None,
    display: EpgDisplay | None = None,
    epg_shifts_path: Path | None = None,
    favorites: set[str] | None = None,
    favorites_path: Path | None = None,
    favorites_feed: str | None = None,
    record_dir: Path | None = None,
    schedule: list[ScheduledRecording] | None = None,
    schedule_path: Path | None = None,
    live_buffer_minutes: float = DEFAULT_LIVE_BUFFER_MINUTES,
    playback_positions: dict[str, float] | None = None,
    playback_positions_path: Path | None = None,
) -> int:
    player = Player(**live_buffer_mpv_options(live_buffer_minutes))
    hide_timer: threading.Timer | None = None
    resize_timer: threading.Timer | None = None
    last_mouse_trigger = float("-inf")
    guide_visible = False
    guide_window_start: datetime | None = None
    selected_channel_url: str | None = None
    details_visible = False
    details_channel: Channel | None = None
    details_programme: Programme | None = None
    aspect_index = 0
    recording_path: Path | None = None
    guide_filter = ""
    filter_input_active = False
    filter_input_text = ""
    favorites_only = False
    favorites = favorites if favorites is not None else set()
    schedule_list = list(schedule) if schedule is not None else []
    active_schedule: ScheduledRecording | None = None
    schedule_stop_event = threading.Event()
    missed_schedule: list[tuple[ScheduledRecording, str]] = []
    missed_reasons: dict[str, str] = {}
    recordings_visible = False
    recordings_list: list[RecordingFile] = []
    recordings_selected_path: Path | None = None
    recordings_pending_delete_path: Path | None = None
    recordings_delete_timer: threading.Timer | None = None
    playing_recording: RecordingFile | None = None
    live_pause_timer: threading.Timer | None = None
    playback_positions = dict(playback_positions) if playback_positions is not None else {}
    playback_positions_path = playback_positions_path or DEFAULT_PLAYBACK_POSITIONS_PATH
    playback_autosave_stop_event = threading.Event()
    schedule_browser_visible = False
    schedule_browser_selected_id: str | None = None
    help_visible = False

    def cancel_hide_timer() -> None:
        nonlocal hide_timer
        if hide_timer is not None:
            hide_timer.cancel()
            hide_timer = None

    def cancel_resize_timer() -> None:
        nonlocal resize_timer
        if resize_timer is not None:
            resize_timer.cancel()
            resize_timer = None

    def cycle_aspect_ratio() -> None:
        nonlocal aspect_index
        aspect_index = (aspect_index + 1) % len(_ASPECT_RATIOS)
        ratio, label = _ASPECT_RATIOS[aspect_index]
        player.set_video_aspect(ratio)
        player.show_text(f"Aspect ratio: {label}", duration_ms=2000)
        logger.info("Aspect ratio -> %s", label)

    def _persist_schedule() -> None:
        if schedule_path is None:
            return
        try:
            save_schedule(schedule_path, schedule_list)
        except OSError as exc:
            print(f"Warning: could not save schedule to {schedule_path}: {exc}", file=sys.stderr)
            logger.warning("Could not save schedule to %s: %s", schedule_path, exc)

    def toggle_recording() -> None:
        nonlocal recording_path, schedule_list, active_schedule
        if player.is_recording:
            player.stop_recording()
            player.show_text(f"Recording saved: {recording_path.name}", duration_ms=3000)
            logger.info("Recording stopped: %s", recording_path)
            recording_path = None
            if active_schedule is not None:
                # Manually stopped a recording that a schedule started --
                # it's been fulfilled (if only partially), so don't let the
                # poll loop try to act on it again later.
                schedule_list = [s for s in schedule_list if s.id != active_schedule.id]
                _persist_schedule()
                active_schedule = None
                if guide_visible:
                    render_and_show_guide()
                if schedule_browser_visible:
                    render_and_show_schedule()
            return

        target_dir = record_dir or DEFAULT_RECORDINGS_DIR
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            player.show_text(f"Could not start recording: {exc}", duration_ms=4000)
            logger.error("Could not create recording directory %s: %s", target_dir, exc)
            return

        label = channel.name if channel is not None else (title or "stream")
        recording_path = target_dir / recording_filename(label, datetime.now())
        player.start_recording(str(recording_path))
        player.show_text(f"Recording to {recording_path.name}", duration_ms=3000)
        logger.info("Recording started: %s", recording_path)

    def close_help_overlay() -> None:
        nonlocal help_visible
        if not help_visible:
            return
        player.clear_overlay(overlay_id=_HELP_OVERLAY_ID)
        player.unbind_key("ESC")
        help_visible = False
        logger.info("Help overlay closed")

    def open_help_overlay() -> None:
        nonlocal help_visible
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_help_overlay(osd_size[0], osd_size[1])
        x = (osd_size[0] - image.width) // 2
        y = (osd_size[1] - image.height) // 2
        player.show_overlay(image, x=x, y=y, overlay_id=_HELP_OVERLAY_ID)
        player.on_key_press("ESC", close_help_overlay)
        help_visible = True
        logger.info("Help overlay opened")

    def toggle_help_overlay() -> None:
        # '?' isn't one of the a-z/0-9 keys the guide filter's text-entry
        # shadows (see _FILTER_INPUT_CHARS), so it stays bound while
        # typing a filter query -- guard here instead, rather than
        # interrupting that to open/close an unrelated overlay.
        if filter_input_active:
            return
        if help_visible:
            close_help_overlay()
            return
        if guide_visible:
            close_guide()
        if recordings_visible:
            close_recordings_browser()
        if schedule_browser_visible:
            close_schedule_browser()
        open_help_overlay()

    def cancel_live_pause_timer() -> None:
        nonlocal live_pause_timer
        if live_pause_timer is not None:
            live_pause_timer.cancel()
            live_pause_timer = None

    def _auto_resume_live_pause() -> None:
        nonlocal live_pause_timer
        live_pause_timer = None
        if player.is_paused:
            player.set_paused(False)
            player.show_text("Resumed automatically (buffer limit reached)", duration_ms=4000)
            logger.info("Live TV auto-resumed after reaching the %.0f-minute pause limit", live_buffer_minutes)

    def toggle_live_pause() -> None:
        nonlocal live_pause_timer
        if player.is_paused:
            player.set_paused(False)
            cancel_live_pause_timer()
            player.show_text("Resumed", duration_ms=2000)
            logger.info("Playback resumed")
            return

        player.set_paused(True)
        if playing_recording is None:
            # A live channel -- the demuxer cache keeps buffering in the
            # background while paused (see live_buffer_mpv_options), so
            # resuming continues from here rather than jumping back to
            # live; cap how long that can go on for, rather than letting
            # it grow unbounded.
            player.show_text(f"Paused (resumes automatically after {live_buffer_minutes:.0f} min)", duration_ms=4000)
            cancel_live_pause_timer()
            live_pause_timer = threading.Timer(live_buffer_minutes * 60, _auto_resume_live_pause)
            live_pause_timer.daemon = True
            live_pause_timer.start()
        else:
            # A recording played back from disk is already fully seekable
            # with no buffer to run out of -- a plain pause, no timer.
            player.show_text("Paused", duration_ms=2000)
        logger.info("Playback paused")

    def _save_current_recording_position() -> None:
        # Called whenever we're about to stop watching whatever recording
        # is currently playing (switching to another recording, tuning to
        # a live channel, or quitting) -- so reopening it later (see
        # play_selected_recording) can resume from here instead of
        # starting over. Barely-started or effectively-finished positions
        # are dropped rather than saved, so a recording you've actually
        # watched doesn't awkwardly "resume" at 0:05 or at the credits.
        if playing_recording is None:
            return
        position_and_duration = player.playback_position()
        if position_and_duration is None:
            return
        position, duration = position_and_duration
        key = str(playing_recording.path)
        if position < _RESUME_MIN_SECONDS or (duration - position) < _RESUME_END_MARGIN_SECONDS:
            playback_positions.pop(key, None)
        else:
            playback_positions[key] = position
        try:
            save_playback_positions(playback_positions_path, playback_positions)
        except OSError as exc:
            logger.warning("Could not save playback position to %s: %s", playback_positions_path, exc)

    def _playback_position_autosave_loop() -> None:
        # A save-on-transition alone (switching recordings/channels) misses
        # the common case: quitting via mpv's own default 'q' shuts down
        # its core before our own cleanup runs, so playback_position() is
        # no longer readable by then -- see the 'finally' block below.
        # Periodically saving while a recording is actually playing means
        # a hard quit loses at most this interval's worth of progress,
        # not everything since the recording was opened.
        while True:
            try:
                if playing_recording is not None:
                    _save_current_recording_position()
            except Exception:
                logger.exception("Error while autosaving playback position")
            if playback_autosave_stop_event.wait(_PLAYBACK_POSITION_AUTOSAVE_SECONDS):
                return

    def handle_playback_error() -> None:
        # A stream that fails to open (dead server, rejected request, etc.)
        # leaves mpv with no video track -- without force_window (see
        # Player.__init__), that would drop the window entirely and, with
        # it, all further keyboard input. Surfacing this and, if there's a
        # guide to fall back on, reopening it keeps the app usable instead
        # of silently stranding the user on a blank, unresponsive window.
        label = channel.name if channel is not None else (title or url)
        player.show_text(f"Failed to play {label}", duration_ms=4000)
        logger.error("Failed to play %s (%s)", label, channel.url if channel is not None else url)
        if channel is not None and display is not None and not guide_visible:
            toggle_guide()

    player.on_playback_error(handle_playback_error)

    logger.info("Starting playback: %s (%s)", title or url, url)
    try:
        player.play(url, title=title)
        player.on_key_press("z", cycle_aspect_ratio)  # available for any playback, not just EPG-backed channels
        player.on_key_press("r", toggle_recording)  # ditto
        player.on_key_press("?", toggle_help_overlay)  # ditto
        player.on_key_press("p", toggle_live_pause)  # ditto
        # PLAY/PAUSE/PLAYPAUSE are the key names mpv reports for the
        # dedicated play/pause button on IR/BLE air-mouse remotes -- mpv's
        # own default binds all three to a plain 'cycle pause' (confirmed
        # via its input-bindings property), which would bypass our own
        # bookkeeping (the live-TV buffer timer, OSD, and treating a
        # recording differently), so it's worth overriding here rather
        # than leaving it to that default.
        player.on_key_press("PLAY", toggle_live_pause)
        player.on_key_press("PAUSE", toggle_live_pause)
        player.on_key_press("PLAYPAUSE", toggle_live_pause)

        playback_autosave_thread = threading.Thread(target=_playback_position_autosave_loop, daemon=True)
        playback_autosave_thread.start()

        if channel is not None and display is not None:
            # A real playlist with no discoverable EPG source (e.g. no
            # x-tvg-url/tvg-url at all) still gets the guide/OSD keybindings
            # -- they just report "no data" instead of silently doing
            # nothing, which otherwise looked indistinguishable from the
            # keys not being bound at all.
            epg = epg or Epg()
            logo = fetch_image(channel.tvg_logo)

            if epg_loader is not None:
                # A large feed can take tens of seconds to download/parse --
                # rather than block playback on that, start the stream first
                # and swap the real data in once it's ready. Reassigning
                # `epg` here (rather than mutating it in place) is what makes
                # this safe to do from another thread: every read below
                # resolves this same closure cell, and a name rebinding is a
                # single atomic pointer swap, so a reader always sees either
                # the placeholder or a fully-loaded Epg, never a half-merged
                # one.
                def _load_epg_in_background() -> None:
                    nonlocal epg
                    loaded = epg_loader()
                    if loaded is not None:
                        epg = loaded
                        print(f"EPG data loaded ({len(loaded.channels)} channels).", file=sys.stderr)
                        logger.info("EPG data loaded (%d channels)", len(loaded.channels))
                    else:
                        print("EPG data not available.", file=sys.stderr)
                        logger.warning("EPG data not available")

                print("Loading EPG data...", file=sys.stderr)
                threading.Thread(target=_load_epg_in_background, daemon=True).start()

            def show_epg_overlay() -> None:
                nonlocal hide_timer
                if guide_visible:
                    # 'i' means "show info" everywhere else in the app; while
                    # the guide is up, that's the selected programme's details.
                    show_selected_details()
                    return
                if recordings_visible:
                    return  # avoid layering the EPG banner over the recordings browser
                cancel_hide_timer()

                if playing_recording is not None:
                    # Watching back a recording (see the 'w' browser), not a
                    # live channel -- there's no EPG to show, so show what's
                    # actually relevant instead: the recording itself and how
                    # far into it we are.
                    canvas_width = _resolve_canvas_width(player)
                    position, duration = player.playback_position() or (None, None)
                    image = render_recording_overlay(
                        playing_recording, canvas_width=canvas_width, position_seconds=position, duration_seconds=duration
                    )
                    player.show_overlay(image, x=0, y=_OVERLAY_TOP_MARGIN)
                    hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
                    hide_timer.daemon = True
                    hide_timer.start()
                    return

                now = datetime.now(timezone.utc)
                current, upcoming = current_and_next_programmes(channel, epg, display, now)
                badges = stream_quality_badges(player.stream_info())
                if current is None and upcoming is None and not badges:
                    # Stream quality badges are independent of EPG data (see
                    # render_epg_overlay's "No programme information" case),
                    # so only bail out here if there's truly nothing at all
                    # to show -- e.g. right after a channel switch, before
                    # mpv has probed the new stream.
                    player.show_text("No EPG data available for this channel", duration_ms=3000)
                    return

                canvas_width = _resolve_canvas_width(player)
                image = render_epg_overlay(
                    channel,
                    current,
                    upcoming,
                    display,
                    now,
                    logo=logo,
                    canvas_width=canvas_width,
                    badges=badges,
                    favorites=favorites,
                )
                # The banner already spans the full video width (see
                # render_epg_overlay), so it's placed flush with the left
                # edge; only the top gets a safe-area gap.
                player.show_overlay(image, x=0, y=_OVERLAY_TOP_MARGIN)

                hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
                hide_timer.daemon = True
                hide_timer.start()

            def on_resize() -> None:
                nonlocal resize_timer
                if hide_timer is None:
                    return  # overlay isn't currently shown; a resize shouldn't pop it back up
                cancel_resize_timer()
                # Debounced: a drag-resize fires many events in quick succession,
                # and re-rendering (logo compositing, text layout) on every one
                # of them would be wasteful and could visibly lag.
                resize_timer = threading.Timer(_OVERLAY_RESIZE_DEBOUNCE_SECONDS, show_epg_overlay)
                resize_timer.daemon = True
                resize_timer.start()

            def on_mouse_move() -> None:
                nonlocal last_mouse_trigger
                # Throttled, not debounced: trackpad/mouse movement fires this
                # continuously (many events per second), and re-rendering on
                # every one would be wasteful -- but unlike resize, we want an
                # immediate response to the first touch, not a delayed one.
                now = time.monotonic()
                if now - last_mouse_trigger < _OVERLAY_MOUSE_MOVE_THROTTLE_SECONDS:
                    return
                last_mouse_trigger = now
                show_epg_overlay()

            def guide_channel_list() -> list[Channel]:
                base = channels or [channel]
                if favorites_only:
                    base = [c for c in base if c.name in favorites]
                if not guide_filter:
                    return base
                needle = guide_filter.lower()
                return [
                    c
                    for c in base
                    if needle in c.name.lower() or any(needle in g.lower() for g in c.groups)
                ]

            def resolved_guide_window_start() -> datetime:
                if guide_window_start is not None:
                    return guide_window_start
                now = datetime.now(timezone.utc)
                return now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 30)

            def render_and_show_guide() -> bool:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_program_guide(
                    guide_channel_list(),
                    epg,
                    display,
                    datetime.now(timezone.utc),
                    current_channel_url=channel.url,
                    canvas_width=osd_size[0],
                    canvas_height=osd_size[1],
                    window_start=guide_window_start,
                    max_rows=_GUIDE_MAX_ROWS,
                    selected_channel_url=selected_channel_url,
                    favorites=favorites,
                    scheduled={(s.channel_url, s.start) for s in schedule_list},
                )
                if image is None:
                    if favorites_only:
                        player.show_text("No favorited channels", duration_ms=3000)
                    elif guide_filter:
                        player.show_text(f"No channels match filter: {guide_filter!r}", duration_ms=3000)
                    else:
                        player.show_text("No programme guide data available", duration_ms=3000)
                    return False

                x = (osd_size[0] - image.width) // 2
                y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
                player.show_overlay(image, x=x, y=y, overlay_id=_GUIDE_OVERLAY_ID)
                return True

            def shift_guide(step: timedelta) -> None:
                nonlocal guide_window_start
                if not guide_visible or details_visible:
                    return  # LEFT/RIGHT are only rebound while the guide is open
                guide_window_start = resolved_guide_window_start() + step
                render_and_show_guide()
                logger.info("Guide window shifted by %s", step)

            def move_guide_selection(step: int) -> None:
                nonlocal selected_channel_url
                if not guide_visible or details_visible:
                    return
                # The full eligible list, not just the currently visible
                # window -- otherwise the cursor clamps at the edge of the
                # displayed rows instead of scrolling the guide to reveal
                # channels further down (or up) the list.
                pool = guide_eligible_channels(guide_channel_list(), epg)
                if not pool:
                    return
                urls = [c.url for c in pool]
                try:
                    index = urls.index(selected_channel_url)
                except ValueError:
                    index = 0
                selected_channel_url = urls[max(0, min(len(urls) - 1, index + step))]
                render_and_show_guide()
                selected = next((c for c in pool if c.url == selected_channel_url), None)
                logger.info("Guide selection -> '%s'", selected.name if selected else selected_channel_url)

            def nudge_selected_shift(step: timedelta) -> None:
                if not guide_visible or details_visible or selected_channel_url is None:
                    return  # '[' / ']' are only rebound while the guide is open, like the other guide keys
                selected_channel = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                if selected_channel is None:
                    return

                new_shift = display.shift_for(selected_channel.name) + step
                display.channel_shifts[selected_channel.name] = new_shift
                if epg_shifts_path is not None:
                    try:
                        save_channel_shifts(epg_shifts_path, display.channel_shifts)
                    except OSError as exc:
                        print(f"Warning: could not save EPG shift to {epg_shifts_path}: {exc}", file=sys.stderr)
                        logger.warning("Could not save EPG shift to %s: %s", epg_shifts_path, exc)

                render_and_show_guide()
                player.show_text(f"{selected_channel.name} shift: {format_time_shift(new_shift)}", duration_ms=1500)
                logger.info("EPG shift for '%s' -> %s", selected_channel.name, format_time_shift(new_shift))

            def reset_guide_selection() -> None:
                nonlocal selected_channel_url
                # Called after the eligible channel list changes shape (a
                # filter applied/cleared) -- keeps the playing channel
                # selected if it's still eligible, else falls back to
                # whatever's first, mirroring toggle_guide's initial pick.
                pool = guide_eligible_channels(guide_channel_list(), epg)
                urls = [c.url for c in pool]
                selected_channel_url = channel.url if channel.url in urls else (urls[0] if urls else None)

            def bind_guide_navigation_keys() -> None:
                # These keys normally seek/do nothing; rebinding them here
                # (and unbinding in unbind_guide_navigation_keys) scopes
                # guide navigation to only while the guide is on screen.
                player.on_key_press("LEFT", lambda: shift_guide(-_GUIDE_TIME_STEP))
                player.on_key_press("RIGHT", lambda: shift_guide(_GUIDE_TIME_STEP))
                player.on_key_press("UP", lambda: move_guide_selection(-1))
                player.on_key_press("DOWN", lambda: move_guide_selection(1))
                player.on_key_press("PGUP", lambda: move_guide_selection(-_GUIDE_MAX_ROWS))
                player.on_key_press("PGDWN", lambda: move_guide_selection(_GUIDE_MAX_ROWS))
                player.on_key_press("ENTER", switch_to_selected_channel)
                player.on_key_press("KP_ENTER", switch_to_selected_channel)
                player.on_key_press("[", lambda: nudge_selected_shift(-_SHIFT_NUDGE_STEP))
                player.on_key_press("]", lambda: nudge_selected_shift(_SHIFT_NUDGE_STEP))
                player.on_key_press("f", start_guide_filter_input)
                player.on_key_press("c", clear_guide_filter)
                player.on_key_press("v", toggle_favorites_only)

            def unbind_guide_navigation_keys() -> None:
                for key in (*_GUIDE_NAV_ONLY_KEYS, "ENTER", "KP_ENTER", "f", "c", "v"):
                    player.unbind_key(key)

            def render_filter_prompt() -> None:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_guide_filter_prompt(filter_input_text, osd_size[0], osd_size[1])
                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_FILTER_OVERLAY_ID)

            def append_filter_char(char: str) -> None:
                nonlocal filter_input_text
                filter_input_text += char
                render_filter_prompt()

            def remove_filter_char() -> None:
                nonlocal filter_input_text
                filter_input_text = filter_input_text[:-1]
                render_filter_prompt()

            def finish_filter_input() -> None:
                nonlocal filter_input_active
                filter_input_active = False
                for char in _FILTER_INPUT_CHARS:
                    player.unbind_key(char)
                player.unbind_key("SPACE")
                player.unbind_key("BS")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.clear_overlay(overlay_id=_FILTER_OVERLAY_ID)
                # Restore the always-on bindings the character keyset shadowed
                # (it covers every letter, including g/i/z/h/r/w/u/p's normal meanings).
                player.on_key_press("g", toggle_guide)
                player.on_key_press("i", show_epg_overlay)
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("h", toggle_favorite)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("w", toggle_recordings_browser)
                player.on_key_press("u", toggle_schedule_browser)
                player.on_key_press("p", toggle_live_pause)
                bind_guide_navigation_keys()
                reset_guide_selection()
                render_and_show_guide()

            def confirm_guide_filter() -> None:
                nonlocal guide_filter
                guide_filter = filter_input_text.strip()
                finish_filter_input()
                logger.info("Guide filter set to %r", guide_filter)

            def cancel_guide_filter() -> None:
                finish_filter_input()
                logger.info("Guide filter input cancelled")

            def start_guide_filter_input() -> None:
                nonlocal filter_input_active, filter_input_text
                if not guide_visible or details_visible or filter_input_active:
                    return  # 'f' is only bound while the guide is open, like the other guide keys
                filter_input_active = True
                filter_input_text = ""
                unbind_guide_navigation_keys()
                for char in _FILTER_INPUT_CHARS:
                    player.on_key_press(char, lambda char=char: append_filter_char(char))
                player.on_key_press("SPACE", lambda: append_filter_char(" "))
                player.on_key_press("BS", remove_filter_char)
                player.on_key_press("ENTER", confirm_guide_filter)
                player.on_key_press("KP_ENTER", confirm_guide_filter)
                player.on_key_press("ESC", cancel_guide_filter)
                render_filter_prompt()
                logger.info("Guide filter input started")

            def clear_guide_filter() -> None:
                nonlocal guide_filter
                if not guide_visible or details_visible or filter_input_active or not guide_filter:
                    return  # 'c' is only bound while the guide is open, like the other guide keys
                guide_filter = ""
                reset_guide_selection()
                render_and_show_guide()
                logger.info("Guide filter cleared")

            def toggle_favorites_only() -> None:
                nonlocal favorites_only
                if not guide_visible or details_visible:
                    return  # 'v' is only bound while the guide is open, like the other guide keys
                favorites_only = not favorites_only
                reset_guide_selection()
                if render_and_show_guide():
                    # Only when there was something to show -- render_and_show_guide
                    # already gives its own "No favorited channels" feedback otherwise.
                    player.show_text("Favorites only" if favorites_only else "All channels", duration_ms=1500)
                logger.info("Guide favorites-only view: %s", favorites_only)

            def close_details() -> None:
                nonlocal details_visible, details_channel, details_programme
                if not details_visible:
                    return
                player.clear_overlay(overlay_id=_DETAILS_OVERLAY_ID)
                player.unbind_key("ESC")
                player.unbind_key("s")
                details_visible = False
                details_channel = None
                details_programme = None
                logger.info("Programme details closed")

            def show_selected_details() -> None:
                nonlocal details_visible, details_channel, details_programme
                if not guide_visible or details_visible or selected_channel_url is None:
                    return

                selected_channel = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                if selected_channel is None:
                    return
                reference_time = guide_reference_time(datetime.now(timezone.utc), resolved_guide_window_start())
                programme = selected_guide_programme(
                    epg,
                    selected_channel.tvg_id,
                    reference_time,
                    shift=display.shift_for(selected_channel.name),
                    name=selected_channel.tvg_name or selected_channel.name,
                )
                if programme is None:
                    return

                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_programme_details(
                    selected_channel,
                    programme,
                    display,
                    osd_size[0],
                    osd_size[1],
                    logo=fetch_image(selected_channel.tvg_logo),
                )
                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_DETAILS_OVERLAY_ID)
                details_visible = True
                details_channel = selected_channel
                details_programme = programme
                player.on_key_press("ESC", close_details)  # only bound while the popup is open
                player.on_key_press("s", toggle_scheduled_recording)  # ditto
                logger.info("Programme details shown: '%s' on '%s'", programme.title, selected_channel.name)

            def toggle_scheduled_recording() -> None:
                nonlocal schedule_list
                if details_channel is None or details_programme is None:
                    return
                programme = details_programme

                existing = next(
                    (
                        s
                        for s in schedule_list
                        if s.channel_url == details_channel.url and s.start == programme.start
                    ),
                    None,
                )
                if existing is not None:
                    schedule_list = [s for s in schedule_list if s.id != existing.id]
                    _persist_schedule()
                    render_and_show_guide()  # refresh the badge in the guide underneath, without waiting for a cursor move
                    player.show_text(f"Recording cancelled: {programme.title}", duration_ms=3000)
                    logger.info("Scheduled recording cancelled: '%s' on '%s'", programme.title, details_channel.name)
                    return

                shift = display.shift_for(details_channel.name)
                if programme.stop + shift <= datetime.now(timezone.utc):
                    player.show_text("That programme has already ended", duration_ms=3000)
                    return

                entry = ScheduledRecording.create(
                    channel_url=details_channel.url,
                    channel_name=details_channel.name,
                    title=programme.title,
                    start=programme.start,
                    stop=programme.stop,
                )
                schedule_list = [*schedule_list, entry]
                _persist_schedule()
                render_and_show_guide()  # refresh the badge in the guide underneath, without waiting for a cursor move
                player.show_text(f"Recording scheduled: {programme.title}", duration_ms=3000)
                logger.info(
                    "Scheduled recording: '%s' on '%s' (%s - %s)",
                    programme.title,
                    details_channel.name,
                    entry.start,
                    entry.stop,
                )

            def close_guide() -> None:
                nonlocal guide_visible
                if not guide_visible:
                    return
                close_details()
                player.clear_overlay(overlay_id=_GUIDE_OVERLAY_ID)
                unbind_guide_navigation_keys()
                player.on_key_press("ENTER", show_epg_overlay)  # restore the base binding just removed above
                guide_visible = False
                logger.info("Guide closed")

            def switch_to_channel(new_channel: Channel) -> None:
                nonlocal channel, logo, playing_recording
                _save_current_recording_position()
                channel = new_channel
                logo = fetch_image(channel.tvg_logo)
                playing_recording = None  # back to live TV -- 'i' should show its EPG info again, not a stale recording
                player.play(channel.url, title=channel.name)
                show_epg_overlay()
                logger.info("Switched to channel '%s' (%s)", channel.name, channel.url)

            def switch_to_selected_channel() -> None:
                if not guide_visible or selected_channel_url is None:
                    return
                new_channel = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                if new_channel is None:
                    return

                close_guide()
                switch_to_channel(new_channel)

            def _run_scheduled_recording(entry: ScheduledRecording) -> None:
                nonlocal recording_path, active_schedule
                if player.is_recording:
                    logger.warning(
                        "Skipping scheduled recording '%s' on '%s': already recording something else",
                        entry.title,
                        entry.channel_name,
                    )
                    missed_reasons[entry.id] = "another recording was already using the tuner"
                    return

                target = next((c for c in (channels or [channel]) if c.url == entry.channel_url), None)
                if target is None:
                    logger.warning(
                        "Skipping scheduled recording '%s': channel '%s' isn't in this playlist",
                        entry.title,
                        entry.channel_name,
                    )
                    missed_reasons[entry.id] = "its channel isn't in this playlist"
                    return

                if target.url != channel.url:
                    switch_to_channel(target)

                target_dir = record_dir or DEFAULT_RECORDINGS_DIR
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    logger.error("Could not create recording directory %s: %s", target_dir, exc)
                    return

                recording_path = target_dir / recording_filename(f"{target.name} - {entry.title}", datetime.now())
                player.start_recording(str(recording_path))
                active_schedule = entry
                player.show_text(f"Recording: {entry.title}", duration_ms=4000)
                logger.info(
                    "Scheduled recording started: '%s' on '%s' -> %s", entry.title, target.name, recording_path
                )

            def _finish_scheduled_recording() -> None:
                nonlocal recording_path, active_schedule
                if active_schedule is None:
                    return
                player.stop_recording()
                player.show_text(f"Recording finished: {active_schedule.title}", duration_ms=4000)
                logger.info("Scheduled recording finished: '%s' -> %s", active_schedule.title, recording_path)
                recording_path = None
                active_schedule = None

            def _schedule_poll_tick() -> None:
                nonlocal schedule_list
                now = datetime.now(timezone.utc)

                if active_schedule is not None and schedule_window(active_schedule, display)[1] <= now:
                    finished = active_schedule
                    _finish_scheduled_recording()
                    schedule_list = [s for s in schedule_list if s.id != finished.id]
                    _persist_schedule()
                    if guide_visible:
                        render_and_show_guide()
                    if schedule_browser_visible:
                        render_and_show_schedule()

                if active_schedule is None:
                    due = min(
                        (s for s in schedule_list if schedule_window(s, display)[0] <= now < schedule_window(s, display)[1]),
                        key=lambda s: schedule_window(s, display)[0],
                        default=None,
                    )
                    if due is not None:
                        _run_scheduled_recording(due)
                        if schedule_browser_visible:
                            render_and_show_schedule()

                # Anything left whose stop time has passed never got to run
                # (e.g. its channel wasn't in this playlist, or another
                # recording was already using the one available "tuner") --
                # no point retrying it forever.
                missed = [s for s in schedule_list if schedule_window(s, display)[1] <= now and s is not active_schedule]
                if missed:
                    for s in missed:
                        reason = missed_reasons.pop(s.id, "another recording was already using the tuner")
                        logger.warning(
                            "Scheduled recording never started (missed): '%s' on '%s' (%s)",
                            s.title,
                            s.channel_name,
                            reason,
                        )
                        player.show_text(f"Recording missed: {s.title} -- {reason}", duration_ms=5000)
                        missed_schedule.insert(0, (s, reason))
                    del missed_schedule[_MISSED_SCHEDULE_HISTORY_LIMIT:]
                    schedule_list = [s for s in schedule_list if s not in missed]
                    _persist_schedule()
                    if guide_visible:
                        render_and_show_guide()
                    if schedule_browser_visible:
                        render_and_show_schedule()

            def _schedule_poll_loop() -> None:
                while True:
                    try:
                        _schedule_poll_tick()
                    except Exception:
                        # A background thread with no supervisor: an uncaught
                        # exception here would otherwise silently kill all
                        # future scheduled recordings for the rest of this
                        # run, with no visible symptom until a show quietly
                        # fails to record.
                        logger.exception("Error while checking scheduled recordings")
                    if schedule_stop_event.wait(_SCHEDULE_POLL_SECONDS):
                        return

            schedule_thread = threading.Thread(target=_schedule_poll_loop, daemon=True)
            schedule_thread.start()

            def cancel_recordings_delete_timer() -> None:
                nonlocal recordings_delete_timer
                if recordings_delete_timer is not None:
                    recordings_delete_timer.cancel()
                    recordings_delete_timer = None

            def close_recordings_browser() -> None:
                nonlocal recordings_visible, recordings_selected_path, recordings_pending_delete_path
                if not recordings_visible:
                    return
                cancel_recordings_delete_timer()
                recordings_pending_delete_path = None
                player.clear_overlay(overlay_id=_RECORDINGS_OVERLAY_ID)
                player.unbind_key("UP")
                player.unbind_key("DOWN")
                player.unbind_key("PGUP")
                player.unbind_key("PGDWN")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.unbind_key("d")
                player.on_key_press("ENTER", show_epg_overlay)  # restore the base binding just removed above
                recordings_visible = False
                recordings_selected_path = None
                logger.info("Recordings browser closed")

            def render_and_show_recordings() -> bool:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_recordings_browser(
                    recordings_list,
                    recordings_selected_path,
                    osd_size[0],
                    osd_size[1],
                    max_rows=_RECORDINGS_MAX_ROWS,
                )
                if image is None:
                    return False
                x = (osd_size[0] - image.width) // 2
                y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
                player.show_overlay(image, x=x, y=y, overlay_id=_RECORDINGS_OVERLAY_ID)
                return True

            def move_recordings_selection(step: int) -> None:
                nonlocal recordings_selected_path
                if not recordings_visible or not recordings_list:
                    return
                paths = [r.path for r in recordings_list]
                try:
                    index = paths.index(recordings_selected_path)
                except ValueError:
                    index = 0
                recordings_selected_path = paths[max(0, min(len(paths) - 1, index + step))]
                render_and_show_recordings()

            def play_selected_recording() -> None:
                nonlocal playing_recording
                if not recordings_visible or recordings_selected_path is None:
                    return
                selected = next((r for r in recordings_list if r.path == recordings_selected_path), None)
                if selected is None:
                    return
                close_recordings_browser()
                _save_current_recording_position()  # in case we were already watching a different one
                playing_recording = selected
                resume_at = playback_positions.get(str(selected.path))
                player.play(str(selected.path), title=selected.label, start=resume_at)
                if resume_at:
                    player.show_text(f"Resuming: {selected.label}", duration_ms=3000)
                    logger.info("Resuming recording at %.0fs: %s", resume_at, selected.path)
                else:
                    player.show_text(f"Playing recording: {selected.label}", duration_ms=3000)
                    logger.info("Playing back recording: %s", selected.path)

            def request_delete_recording() -> None:
                # Deleting a recording can't be undone, so this requires
                # pressing 'd' twice: the first press just arms a pending
                # confirmation (cleared automatically after a few seconds,
                # or immediately if the selection moves to a different
                # recording), and only a second 'd' on the *same* still-
                # selected recording actually removes the file.
                nonlocal recordings_list, recordings_selected_path, recordings_pending_delete_path, recordings_delete_timer
                if not recordings_visible or recordings_selected_path is None:
                    return
                selected = next((r for r in recordings_list if r.path == recordings_selected_path), None)
                if selected is None:
                    return

                if recordings_pending_delete_path != selected.path:
                    recordings_pending_delete_path = selected.path
                    player.show_text(f"Press 'd' again to permanently delete: {selected.label}", duration_ms=4000)
                    cancel_recordings_delete_timer()
                    recordings_delete_timer = threading.Timer(4.0, _clear_pending_delete)
                    recordings_delete_timer.daemon = True
                    recordings_delete_timer.start()
                    return

                cancel_recordings_delete_timer()
                recordings_pending_delete_path = None
                try:
                    selected.path.unlink()
                except OSError as exc:
                    player.show_text(f"Could not delete recording: {exc}", duration_ms=4000)
                    logger.error("Could not delete recording %s: %s", selected.path, exc)
                    return

                logger.info("Deleted recording: %s", selected.path)
                recordings_list = [r for r in recordings_list if r.path != selected.path]
                if not recordings_list:
                    player.show_text(f"Deleted: {selected.label}", duration_ms=3000)
                    close_recordings_browser()
                    return
                recordings_selected_path = recordings_list[0].path
                render_and_show_recordings()
                player.show_text(f"Deleted: {selected.label}", duration_ms=3000)

            def _clear_pending_delete() -> None:
                nonlocal recordings_pending_delete_path
                recordings_pending_delete_path = None

            def open_recordings_browser() -> None:
                nonlocal recordings_visible, recordings_list, recordings_selected_path
                recordings_list = list_recordings(record_dir or DEFAULT_RECORDINGS_DIR)
                if not recordings_list:
                    player.show_text("No recordings found", duration_ms=3000)
                    return

                recordings_selected_path = recordings_list[0].path
                if render_and_show_recordings():
                    recordings_visible = True
                    player.on_key_press("UP", lambda: move_recordings_selection(-1))
                    player.on_key_press("DOWN", lambda: move_recordings_selection(1))
                    player.on_key_press("PGUP", lambda: move_recordings_selection(-_RECORDINGS_MAX_ROWS))
                    player.on_key_press("PGDWN", lambda: move_recordings_selection(_RECORDINGS_MAX_ROWS))
                    player.on_key_press("ENTER", play_selected_recording)
                    player.on_key_press("KP_ENTER", play_selected_recording)
                    player.on_key_press("ESC", close_recordings_browser)
                    player.on_key_press("d", request_delete_recording)
                    logger.info("Recordings browser opened (%d recordings)", len(recordings_list))

            def toggle_recordings_browser() -> None:
                if recordings_visible:
                    close_recordings_browser()
                    return
                if guide_visible:
                    close_guide()
                if schedule_browser_visible:
                    close_schedule_browser()
                if help_visible:
                    close_help_overlay()
                open_recordings_browser()

            def close_schedule_browser() -> None:
                nonlocal schedule_browser_visible, schedule_browser_selected_id
                if not schedule_browser_visible:
                    return
                player.clear_overlay(overlay_id=_SCHEDULE_OVERLAY_ID)
                player.unbind_key("UP")
                player.unbind_key("DOWN")
                player.unbind_key("PGUP")
                player.unbind_key("PGDWN")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.on_key_press("ENTER", show_epg_overlay)  # restore the base binding just removed above
                schedule_browser_visible = False
                schedule_browser_selected_id = None
                logger.info("Scheduled recordings browser closed")

            def render_and_show_schedule() -> bool:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                ordered = sorted(schedule_list, key=lambda s: s.start)
                image = render_schedule_browser(
                    ordered,
                    schedule_browser_selected_id,
                    display,
                    osd_size[0],
                    osd_size[1],
                    max_rows=_SCHEDULE_MAX_ROWS,
                    active_id=active_schedule.id if active_schedule is not None else None,
                    missed=missed_schedule,
                )
                if image is None:
                    return False
                x = (osd_size[0] - image.width) // 2
                y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
                player.show_overlay(image, x=x, y=y, overlay_id=_SCHEDULE_OVERLAY_ID)
                return True

            def move_schedule_selection(step: int) -> None:
                nonlocal schedule_browser_selected_id
                if not schedule_browser_visible:
                    return
                ordered = sorted(schedule_list, key=lambda s: s.start)
                if not ordered:
                    return
                ids = [s.id for s in ordered]
                try:
                    index = ids.index(schedule_browser_selected_id)
                except ValueError:
                    index = 0
                schedule_browser_selected_id = ids[max(0, min(len(ids) - 1, index + step))]
                render_and_show_schedule()

            def cancel_selected_schedule_entry() -> None:
                nonlocal schedule_list, schedule_browser_selected_id
                if not schedule_browser_visible or schedule_browser_selected_id is None:
                    return
                selected = next((s for s in schedule_list if s.id == schedule_browser_selected_id), None)
                if selected is None:
                    return
                if active_schedule is not None and selected.id == active_schedule.id:
                    player.show_text(
                        "Can't cancel a recording already in progress -- stop it with 'r' instead", duration_ms=4000
                    )
                    return

                schedule_list = [s for s in schedule_list if s.id != selected.id]
                _persist_schedule()
                player.show_text(f"Recording cancelled: {selected.title}", duration_ms=3000)
                logger.info("Scheduled recording cancelled: '%s' on '%s'", selected.title, selected.channel_name)

                ordered = sorted(schedule_list, key=lambda s: s.start)
                if not ordered:
                    close_schedule_browser()
                    return
                schedule_browser_selected_id = ordered[0].id
                render_and_show_schedule()

            def open_schedule_browser() -> None:
                nonlocal schedule_browser_visible, schedule_browser_selected_id
                if not schedule_list and not missed_schedule:
                    player.show_text("No scheduled recordings", duration_ms=3000)
                    return

                ordered = sorted(schedule_list, key=lambda s: s.start)
                schedule_browser_selected_id = ordered[0].id if ordered else None
                if render_and_show_schedule():
                    schedule_browser_visible = True
                    player.on_key_press("UP", lambda: move_schedule_selection(-1))
                    player.on_key_press("DOWN", lambda: move_schedule_selection(1))
                    player.on_key_press("PGUP", lambda: move_schedule_selection(-_SCHEDULE_MAX_ROWS))
                    player.on_key_press("PGDWN", lambda: move_schedule_selection(_SCHEDULE_MAX_ROWS))
                    player.on_key_press("ENTER", cancel_selected_schedule_entry)
                    player.on_key_press("KP_ENTER", cancel_selected_schedule_entry)
                    player.on_key_press("ESC", close_schedule_browser)
                    logger.info("Scheduled recordings browser opened (%d entries)", len(schedule_list))

            def toggle_schedule_browser() -> None:
                if schedule_browser_visible:
                    close_schedule_browser()
                    return
                if guide_visible:
                    close_guide()
                if recordings_visible:
                    close_recordings_browser()
                if help_visible:
                    close_help_overlay()
                open_schedule_browser()

            def toggle_guide() -> None:
                nonlocal guide_visible, guide_window_start, selected_channel_url, guide_filter, favorites_only
                if guide_visible:
                    close_guide()
                    return
                if recordings_visible:
                    close_recordings_browser()
                if schedule_browser_visible:
                    close_schedule_browser()
                if help_visible:
                    close_help_overlay()

                # Showing the guide replaces the small info banner rather than
                # layering on top of it, and always opens on the current time
                # with any previous filter cleared.
                cancel_hide_timer()
                player.clear_overlay()
                guide_window_start = None
                guide_filter = ""
                favorites_only = False

                visible = visible_guide_channels(guide_channel_list(), epg, channel.url, max_rows=_GUIDE_MAX_ROWS)
                urls = [c.url for c in visible]
                selected_channel_url = channel.url if channel.url in urls else (urls[0] if urls else None)

                if render_and_show_guide():
                    guide_visible = True
                    bind_guide_navigation_keys()
                    logger.info("Guide opened")

            def toggle_favorite() -> None:
                # Acts on the guide's selected channel while it's open, or
                # the currently-playing one otherwise -- one binding for
                # both, rather than shadowing it like the guide-only keys,
                # since which channel it should act on is the only thing
                # that changes.
                if guide_visible and selected_channel_url is not None:
                    target = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                else:
                    target = channel
                if target is None:
                    return

                if target.name in favorites:
                    favorites.discard(target.name)
                    action = "Removed from favorites"
                else:
                    favorites.add(target.name)
                    action = "Added to favorites"

                if favorites_path is not None and favorites_feed is not None:
                    try:
                        save_favorites(favorites_path, favorites_feed, favorites)
                    except OSError as exc:
                        print(f"Warning: could not save favorites to {favorites_path}: {exc}", file=sys.stderr)
                        logger.warning("Could not save favorites to %s: %s", favorites_path, exc)

                player.show_text(f"{action}: {target.name}", duration_ms=1500)
                logger.info("%s: '%s'", action, target.name)
                if guide_visible:
                    render_and_show_guide()
                elif hide_timer is not None:
                    # The EPG banner is currently up (e.g. 'i' then 'h') --
                    # redraw it so its heart marker reflects the toggle
                    # immediately, rather than showing a stale one until the
                    # next resize/mouse-move/'i' press.
                    show_epg_overlay()

            show_epg_overlay()
            # 'i' shows EPG info: the small banner normally, or the selected
            # programme's details while the guide is open (see show_epg_overlay).
            player.on_key_press("i", show_epg_overlay)
            # The OK/center button on IR/BLE air-mouse remotes (e.g. nRF-based
            # USB dongles) typically sends ENTER -- mirrors 'i' so pressing it
            # shows the EPG overlay. Shadowed by bind_guide_navigation_keys's
            # own ENTER binding (select the highlighted channel) while the
            # guide is open, and restored by close_guide once it isn't.
            player.on_key_press("ENTER", show_epg_overlay)
            player.on_resize(on_resize)  # keep the overlay correctly sized as the window is resized
            player.on_key_press("MOUSE_MOVE", on_mouse_move)  # trackpad/mouse activity reveals it too
            player.on_key_press("g", toggle_guide)  # press 'g' to toggle the full program guide
            player.on_key_press("h", toggle_favorite)  # 'h' (heart) favorites the playing/selected channel
            player.on_key_press("w", toggle_recordings_browser)  # 'w' (watch) browses past recordings
            player.on_key_press("u", toggle_schedule_browser)  # 'u' (upcoming) browses scheduled recordings
            # The MENU button on IR/BLE air-mouse remotes sends MENU (mpv's
            # own default binds it to the on-screen 'select' script's menu --
            # harmless to override, since this app doesn't use that script).
            # Unlike ENTER, MENU isn't a guide-only key anywhere else, so no
            # shadowing/restoring is needed -- this is simply a permanent
            # second alias for 'g'.
            player.on_key_press("MENU", toggle_guide)

        player.wait_for_playback()
    except KeyboardInterrupt:
        logger.info("Interrupted (Ctrl-C)")
    finally:
        cancel_hide_timer()
        cancel_resize_timer()
        cancel_live_pause_timer()
        try:
            # Player.playback_position() already treats mpv's core being
            # mid-shutdown (e.g. the user quit via its own default 'q') as
            # "not available" rather than raising -- this is just a last
            # line of defense so a genuinely unexpected error here can
            # never skip player.quit() below.
            _save_current_recording_position()
        except Exception:
            logger.exception("Could not save playback position on shutdown")
        playback_autosave_stop_event.set()
        schedule_stop_event.set()
        player.quit()
        logger.info("Shutting down")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tvdinner",
        description="Play IPTV streams from an M3U playlist or a direct stream URL. "
        "Run 'tvdinner bookmarks' instead to manage and launch saved playlist bookmarks, "
        "'tvdinner backup' to save configuration to a single archive, or "
        "'tvdinner restore' to restore it.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "url",
        help="M3U/M3U8 playlist URL or local file path, or a direct video/audio stream URL",
    )
    parser.add_argument(
        "-c",
        "--channel",
        help="Channel name (or 1-based index) to play; defaults to the first channel in the playlist",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List channels in the playlist and exit without playing",
    )
    parser.add_argument(
        "--epg",
        metavar="URL",
        help="XMLTV EPG URL or local file, overriding any EPG source discovered in the M3U playlist",
    )
    parser.add_argument(
        "--tz",
        metavar="NAME",
        help="IANA timezone for displaying EPG times, e.g. 'Europe/London' (default: system local timezone)",
    )
    parser.add_argument(
        "--time-shift",
        metavar="SHIFT",
        help="Correct EPG feed clock errors, e.g. '+1h', '-30m', or minutes as a plain integer; "
        "applies to any channel without its own override in --epg-shifts",
    )
    parser.add_argument(
        "--epg-shifts",
        metavar="PATH",
        help="JSON file mapping a channel's display name (see --list) to a per-channel "
        f"EPG time-shift override (default: {DEFAULT_CHANNEL_SHIFTS_PATH}); also updated "
        "live by the '[' / ']' guide keybinding",
    )
    parser.add_argument(
        "--favorites",
        metavar="PATH",
        help="JSON file storing favorited channels per playlist (see the 'h' guide/playback "
        f"keybinding, default: {DEFAULT_FAVORITES_PATH})",
    )
    parser.add_argument(
        "--record-dir",
        metavar="PATH",
        help="Directory to save 'r'-key recordings into -- a raw copy of the stream, not "
        f"re-encoded (default: {DEFAULT_RECORDINGS_DIR})",
    )
    parser.add_argument(
        "--schedule-file",
        metavar="PATH",
        help="JSON file storing EPG-scheduled recordings (see the 's' guide keybinding, "
        f"default: {DEFAULT_SCHEDULE_PATH}); tvdinner must still be running when a scheduled "
        "recording's time arrives -- there's no background service",
    )
    parser.add_argument(
        "--live-buffer-minutes",
        type=float,
        default=DEFAULT_LIVE_BUFFER_MINUTES,
        metavar="MINUTES",
        help="How long the 'p' keybinding can pause a live channel before it resumes "
        f"automatically (default: {DEFAULT_LIVE_BUFFER_MINUTES:.0f}); resuming (manually or "
        "automatically) continues from the paused position, not the live edge, so you can "
        "rewind/fast-forward within that window like a DVR",
    )
    parser.add_argument(
        "--playback-positions-file",
        metavar="PATH",
        help="JSON file remembering where you left off in each recording (see the 'w' "
        f"recordings browser), so reopening one resumes instead of starting over (default: "
        f"{DEFAULT_PLAYBACK_POSITIONS_PATH})",
    )
    parser.add_argument(
        "--epg-cache-hours",
        type=float,
        default=24.0,
        metavar="HOURS",
        help="How long a downloaded EPG (--epg or the playlist's own URL) is reused from "
        f"disk before re-fetching (default: 24; cached under {DEFAULT_EPG_CACHE_DIR})",
    )
    parser.add_argument(
        "--no-epg-cache",
        action="store_true",
        help="Always re-download the EPG instead of using a cached copy, and don't write one either",
    )
    parser.add_argument(
        "--refresh-epg-cache",
        action="store_true",
        help="Force a fresh EPG download for this run, ignoring any existing cached copy no "
        "matter its age, then refresh the on-disk cache with it (unlike --no-epg-cache, "
        "later runs still benefit from the cache)",
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log startup/shutdown, user actions, and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Disable file logging entirely",
    )
    return parser


def run_bookmarks_command(argv: list[str]) -> int:
    """Handle `tvdinner bookmarks [...]`: an interactive picker (add/edit/
    delete/select) for saved playlist bookmarks. Selecting one re-enters
    main() with that bookmark's url/epg/channel, exactly as if they'd
    been typed directly."""
    parser = argparse.ArgumentParser(
        prog="tvdinner bookmarks",
        description="Interactively manage and launch saved playlist bookmarks.",
    )
    parser.add_argument(
        "--bookmarks-file",
        metavar="PATH",
        help=f"JSON file storing bookmarks (default: {DEFAULT_BOOKMARKS_PATH})",
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log startup/shutdown, user actions, and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="Disable file logging entirely",
    )
    args = parser.parse_args(argv)
    path = Path(args.bookmarks_file) if args.bookmarks_file else DEFAULT_BOOKMARKS_PATH

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    logger.info("Starting tvdinner %s bookmarks (bookmarks_file=%s)", __version__, path)

    result = run_bookmarks_tui(path)
    if result is None:
        logger.info("Bookmarks closed without selecting one")
        return 0
    selected, refresh_epg = result

    bookmark_argv = [selected.url]
    if selected.epg:
        bookmark_argv += ["--epg", selected.epg]
    if selected.channel:
        bookmark_argv += ["--channel", selected.channel]
    if refresh_epg:
        bookmark_argv += ["--refresh-epg-cache"]
    # Carry this session's logging choice into the launched playback too,
    # so the whole session (browsing bookmarks, then playing one) ends up
    # in one file -- configure_logging() is safe to call again for the
    # same path from within the re-entered main().
    if args.no_log:
        bookmark_argv += ["--no-log"]
    elif args.log_file:
        bookmark_argv += ["--log-file", args.log_file]
    logger.info("Launching bookmark '%s': %s", selected.name, bookmark_argv)
    return main(bookmark_argv)


def _add_config_path_args(parser: argparse.ArgumentParser) -> None:
    """--epg-shifts/--favorites/--bookmarks-file overrides shared by
    `backup` and `restore`, so a backup made from custom paths restores
    to the same custom paths."""
    parser.add_argument(
        "--epg-shifts", metavar="PATH", help=f"EPG shifts file (default: {DEFAULT_CHANNEL_SHIFTS_PATH})"
    )
    parser.add_argument("--favorites", metavar="PATH", help=f"Favorites file (default: {DEFAULT_FAVORITES_PATH})")
    parser.add_argument(
        "--bookmarks-file", metavar="PATH", help=f"Bookmarks file (default: {DEFAULT_BOOKMARKS_PATH})"
    )


def _config_paths(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "epg_shifts.json": Path(args.epg_shifts) if args.epg_shifts else DEFAULT_CHANNEL_SHIFTS_PATH,
        "favorites.json": Path(args.favorites) if args.favorites else DEFAULT_FAVORITES_PATH,
        "bookmarks.json": Path(args.bookmarks_file) if args.bookmarks_file else DEFAULT_BOOKMARKS_PATH,
    }


def run_backup_command(argv: list[str]) -> int:
    """Handle `tvdinner backup [PATH]`: write EPG shifts, favorites, and
    bookmarks into a single zip archive for offline storage or moving to
    another machine. The EPG cache and log file are deliberately left
    out -- they're disposable, not configuration."""
    parser = argparse.ArgumentParser(
        prog="tvdinner backup",
        description="Back up tvdinner's configuration files into a single compressed archive.",
    )
    parser.add_argument(
        "output",
        nargs="?",
        metavar="PATH",
        help="Backup archive to create (default: tvdinner-backup-<timestamp>.zip in the current directory)",
    )
    _add_config_path_args(parser)
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log startup/shutdown, user actions, and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--no-log", action="store_true", help="Disable file logging entirely")
    args = parser.parse_args(argv)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)

    output_path = (
        Path(args.output) if args.output else Path(f"tvdinner-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip")
    )
    config_paths = _config_paths(args)
    logger.info("Starting tvdinner %s backup -> %s", __version__, output_path)

    try:
        included = create_backup(output_path, config_paths)
    except OSError as exc:
        print(f"Could not create backup {output_path}: {exc}", file=sys.stderr)
        logger.error("Could not create backup %s: %s", output_path, exc)
        return 1

    if not included:
        print("Warning: no configuration files found to back up.", file=sys.stderr)
        logger.warning("No configuration files found to back up")
    else:
        print(f"Backed up {len(included)} file(s) to {output_path}:")
        for name in included:
            print(f"  {name}")
    logger.info("Backup complete: %s (%s)", output_path, included)
    return 0


def run_restore_command(argv: list[str]) -> int:
    """Handle `tvdinner restore PATH`: extract EPG shifts, favorites, and
    bookmarks from a backup archive, overwriting the current ones.
    Prompts for confirmation unless -y/--yes is given, since this
    replaces existing configuration."""
    parser = argparse.ArgumentParser(
        prog="tvdinner restore",
        description="Restore tvdinner's configuration files from a backup archive, overwriting the current ones.",
    )
    parser.add_argument("input", metavar="PATH", help="Backup archive to restore from")
    _add_config_path_args(parser)
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Don't prompt for confirmation before overwriting"
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log startup/shutdown, user actions, and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--no-log", action="store_true", help="Disable file logging entirely")
    args = parser.parse_args(argv)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)

    input_path = Path(args.input)
    config_paths = _config_paths(args)
    logger.info("Starting tvdinner %s restore <- %s", __version__, input_path)

    if not args.yes:
        answer = input(
            f"This will overwrite tvdinner's current configuration files with the contents of "
            f"{input_path}. Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Restore cancelled.")
            logger.info("Restore cancelled by user")
            return 0

    try:
        restored, unknown = restore_backup(input_path, config_paths)
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"Could not restore from {input_path}: {exc}", file=sys.stderr)
        logger.error("Could not restore from %s: %s", input_path, exc)
        return 1

    for name in unknown:
        print(f"Warning: ignoring unknown entry '{name}' in backup", file=sys.stderr)
        logger.warning("Ignoring unknown entry '%s' in backup %s", name, input_path)

    if not restored:
        print("Warning: no configuration files found in backup.", file=sys.stderr)
        logger.warning("No configuration files found in backup %s", input_path)
    else:
        print(f"Restored {len(restored)} file(s) from {input_path}:")
        for name in restored:
            print(f"  {name}")
    logger.info("Restore complete: %s (%s)", input_path, restored)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    if raw_argv[:1] == ["bookmarks"]:
        return run_bookmarks_command(raw_argv[1:])
    if raw_argv[:1] == ["backup"]:
        return run_backup_command(raw_argv[1:])
    if raw_argv[:1] == ["restore"]:
        return run_restore_command(raw_argv[1:])

    args = build_parser().parse_args(argv)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    logger.info(
        "Starting tvdinner %s (playlist=%s, epg=%s, channel=%s)", __version__, args.url, args.epg, args.channel
    )

    epg_shifts_path = Path(args.epg_shifts) if args.epg_shifts else DEFAULT_CHANNEL_SHIFTS_PATH
    channel_shifts, shift_warnings = load_channel_shifts(epg_shifts_path)
    for warning in shift_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)

    favorites_path = Path(args.favorites) if args.favorites else DEFAULT_FAVORITES_PATH
    favorites, favorites_warnings = load_favorites(favorites_path, args.url)
    for warning in favorites_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)

    record_dir = Path(args.record_dir) if args.record_dir else None

    schedule_path = Path(args.schedule_file) if args.schedule_file else DEFAULT_SCHEDULE_PATH
    schedule_list, schedule_warnings = load_schedule(schedule_path)
    for warning in schedule_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)

    playback_positions_path = (
        Path(args.playback_positions_file) if args.playback_positions_file else DEFAULT_PLAYBACK_POSITIONS_PATH
    )
    playback_positions, playback_position_warnings = load_playback_positions(playback_positions_path)
    for warning in playback_position_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)

    try:
        display = EpgDisplay(
            timezone=resolve_timezone(args.tz),
            default_shift=parse_time_shift(args.time_shift) if args.time_shift else timedelta(),
            channel_shifts=channel_shifts,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        logger.error(str(exc))
        return 1

    playlist = load_playlist(args.url)

    if playlist is None:
        # Doesn't look like an M3U playlist -- treat it as a direct stream URL.
        logger.info("'%s' doesn't look like an M3U playlist; treating it as a direct stream URL", args.url)
        return play_stream(args.url, record_dir=record_dir, live_buffer_minutes=args.live_buffer_minutes)

    logger.info("Loaded playlist: %d channels", len(playlist.channels))

    if not playlist.channels:
        print("No channels found in playlist.", file=sys.stderr)
        logger.error("No channels found in playlist")
        return 1

    epg_cache_dir = None if args.no_epg_cache else DEFAULT_EPG_CACHE_DIR
    # A zero max-age makes any existing cache look expired to both the
    # freshness check in load_epg and the raw-bytes one in
    # _fetch_bytes_cached, so both fall through to a real fetch -- while
    # cache_dir stays set, so the result is still written back to refresh
    # the cache for next time (--no-epg-cache, by contrast, never reads or
    # writes a cache at all).
    epg_max_age = timedelta(0) if args.refresh_epg_cache else timedelta(hours=args.epg_cache_hours)

    if args.list:
        # The channel list is printed once and then the process exits, so
        # there's no later moment for a background load to land -- it has
        # to be fetched synchronously here.
        print("Loading EPG data...", file=sys.stderr)
        epg = load_epg_for_playlist(
            playlist, override=args.epg, cache_dir=epg_cache_dir, max_age=epg_max_age
        )
        if epg is not None:
            print(f"EPG data loaded ({len(epg.channels)} channels).", file=sys.stderr)
            logger.info("EPG data loaded (%d channels)", len(epg.channels))
        else:
            print("EPG data not available.", file=sys.stderr)
            logger.warning("EPG data not available")
        print_channel_list(playlist.channels, epg=epg, display=display)
        return 0

    # EPG data is also shown as an OSD overlay/guide during playback, but
    # loading a large feed can take tens of seconds -- rather than block
    # playback on that, hand play_stream a loader it can run in the
    # background once mpv is already under way.
    def epg_loader() -> Epg | None:
        return load_epg_for_playlist(playlist, override=args.epg, cache_dir=epg_cache_dir, max_age=epg_max_age)

    if args.channel:
        channel = select_channel(playlist.channels, args.channel)
        if channel is None:
            print(f"Channel not found: {args.channel}", file=sys.stderr)
            logger.error("Channel not found: %s", args.channel)
            return 1
    else:
        channel = playlist.channels[0]

    return play_stream(
        channel.url,
        title=channel.name,
        channel=channel,
        channels=playlist.channels,
        epg_loader=epg_loader,
        display=display,
        epg_shifts_path=epg_shifts_path,
        favorites=favorites,
        favorites_path=favorites_path,
        favorites_feed=args.url,
        record_dir=record_dir,
        schedule=schedule_list,
        schedule_path=schedule_path,
        live_buffer_minutes=args.live_buffer_minutes,
        playback_positions=playback_positions,
        playback_positions_path=playback_positions_path,
    )


if __name__ == "__main__":
    sys.exit(main())
