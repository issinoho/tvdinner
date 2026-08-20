"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tvdinner import __version__
from tvdinner.backup import create_backup, restore_backup
from tvdinner.bookmarks import DEFAULT_BOOKMARKS_PATH, load_bookmarks
from tvdinner.bookmarks_tui import run_bookmarks_tui, strip_wrapping_quotes
from tvdinner.channel_logos import CHANNELS_URL, EMPTY_LOGO_INDEX, LOGOS_URL, OnlineLogoIndex, load_online_logo_index
from tvdinner.chromecast import CastDevice, cast_url, chromecast_available, discover_chromecasts, stop_casting
from tvdinner.epg import (
    DEFAULT_CHANNEL_SHIFTS_PATH,
    DEFAULT_EPG_CACHE_DIR,
    Epg,
    EpgDisplay,
    Programme,
    cache_path_for,
    format_time_shift,
    load_channel_shifts,
    load_epg_for_playlist,
    parse_time_shift,
    parsed_cache_path_for,
    resolve_timezone,
    save_channel_shifts,
)
from tvdinner.favorites import DEFAULT_FAVORITES_PATH, load_favorites, save_favorites
from tvdinner.gdrive import (
    BUNDLED_CLIENT_ID,
    BUNDLED_CLIENT_SECRET,
    DEFAULT_GDRIVE_BACKUP_NAME,
    DEFAULT_GDRIVE_TOKEN_PATH,
    GdriveError,
    clear_gdrive_credentials,
    download_backup as download_gdrive_backup,
    load_gdrive_credentials,
    login as gdrive_login,
    save_gdrive_credentials,
    upload_backup as upload_gdrive_backup,
)
from tvdinner.hdhomerun import is_hdhomerun_url, load_hdhomerun_playlist, parse_hdhomerun_url
from tvdinner.history import DEFAULT_HISTORY_PATH, HistoryEntry, HistoryKind, append_history_entry, load_history
from tvdinner.localfile import guess_movie_title_year
from tvdinner.log import DEFAULT_LOG_PATH, close_logging, configure_logging
from tvdinner.m3u import Channel, load_playlist, looks_like_m3u_path
from tvdinner.movietitle import guess_title_year, title_search_candidates
from tvdinner.overlay import (
    DEFAULT_IMAGE_CACHE_DIR,
    guide_eligible_channels,
    guide_reference_time,
    prefetch_channel_logos,
    prefetch_images,
    recording_thumbnail_url,
    render_about_overlay,
    render_cast_picker,
    render_epg_overlay,
    render_guide_filter_prompt,
    render_help_overlay,
    render_history_browser,
    render_plex_browser,
    render_plex_grid_browser,
    render_plex_item_menu,
    render_program_guide,
    render_programme_details,
    render_recording_overlay,
    render_recordings_browser,
    render_schedule_browser,
    render_update_available_overlay,
    render_vod_browser,
    render_vod_info_overlay,
    resolve_channel_logo,
    selected_guide_programme,
    visible_guide_channels,
    visible_guide_movies,
    visible_history_entries,
    visible_plex_grid_nodes,
    visible_plex_nodes,
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
from tvdinner.plex import (
    PlexCreds,
    PlexNode,
    is_plex_url,
    list_plex_libraries,
    list_plex_node_children,
    load_plex_client_id,
    mark_plex_unwatched,
    mark_plex_watched,
    parse_plex_url,
    redact_plex_url,
    report_plex_timeline,
    resolve_plex_playable,
    search_plex,
    search_plex_by_year,
)
from tvdinner.schedule import DEFAULT_SCHEDULE_PATH, ScheduledRecording, load_schedule, save_schedule
from tvdinner.stalker import (
    is_stalker_url,
    load_stalker_playlist,
    load_stalker_vod,
    parse_stalker_url,
    redact_stalker_url,
)
from tvdinner.tmdb import (
    DEFAULT_TMDB_CACHE_DIR,
    DEFAULT_TMDB_CACHE_MAX_AGE,
    fetch_movie_metadata_cached,
    fetch_tv_logo_cached,
    is_movie_category,
    prefetch_backdrop,
    prefetch_director,
    prefetch_logo,
    prefetch_ratings,
)
from tvdinner.tmdb_config import DEFAULT_TMDB_TOKEN_PATH, clear_tmdb_token, load_tmdb_token, save_tmdb_token
from tvdinner.update_check import (
    DEFAULT_UPDATE_CHECK_PATH,
    UpdateInfo,
    check_for_update,
    load_update_check_state,
    save_update_check_state,
    should_check_now,
)
from tvdinner.vod import VodItem, split_m3u_vod_items
from tvdinner.xtream import (
    is_xtream_url,
    load_xtream_playlist,
    load_xtream_vod,
    parse_xtream_url,
    redact_xtream_url,
    xtream_epg_url,
)
from tvdinner.youtube import fetch_youtube_oembed, is_youtube_url

logger = logging.getLogger(__name__)

_OVERLAY_TOP_MARGIN = 40
_GUIDE_BOTTOM_MARGIN = 40
_OVERLAY_HIDE_AFTER_SECONDS = 6.0
_OVERLAY_RESIZE_DEBOUNCE_SECONDS = 0.2
_OVERLAY_MOUSE_MOVE_THROTTLE_SECONDS = 1.0
# Several channel logos on the same guide page typically finish resolving
# within milliseconds of each other -- debounced so that lands as one
# re-render, not a burst of one per completed fetch.
_GUIDE_LOGO_REFRESH_DEBOUNCE_SECONDS = 0.3
_GUIDE_OVERLAY_ID = 1
_DETAILS_OVERLAY_ID = 2
_FILTER_OVERLAY_ID = 3
_RECORDINGS_OVERLAY_ID = 4
_SCHEDULE_OVERLAY_ID = 5
_HELP_OVERLAY_ID = 6
_VOD_OVERLAY_ID = 7
_ABOUT_OVERLAY_ID = 8
_HISTORY_OVERLAY_ID = 9
_GUIDE_TIME_STEP = timedelta(minutes=30)
_SHIFT_NUDGE_STEP = timedelta(minutes=1)
_GUIDE_MAX_ROWS = 8  # kept in sync with render_and_show_guide's max_rows so a page = a full screen
_RECORDINGS_MAX_ROWS = 8  # kept in sync with render_and_show_recordings's max_rows, like _GUIDE_MAX_ROWS
_SCHEDULE_MAX_ROWS = 8  # kept in sync with render_and_show_schedule's max_rows, like _GUIDE_MAX_ROWS
_VOD_MAX_ROWS = 8  # kept in sync with render_and_show_vod's max_rows, like _GUIDE_MAX_ROWS
_HISTORY_MAX_ROWS = 6  # kept in sync with render_and_show_history's max_rows -- shorter than the others, since each row is taller (a thumbnail plus two lines of text)
_MISSED_SCHEDULE_HISTORY_LIMIT = 10  # capped so a long session's conflicts don't grow the 'u' view unbounded
_RESUME_MIN_SECONDS = 10.0  # don't bother resuming a recording barely started
_RESUME_END_MARGIN_SECONDS = 15.0  # this close to the end counts as "fully watched" -- start over, don't resume
_PLAYBACK_POSITION_AUTOSAVE_SECONDS = 10.0  # periodic, not just on switch/quit -- mpv's core is already gone by the
# time the 'finally' block runs after the user quits via mpv's own default 'q', so that alone can't be relied on
# Keys with no meaning outside the guide; suspended while typing a filter
# query too, since they have no character-input equivalent to shadow them.
_GUIDE_NAV_ONLY_KEYS = ("LEFT", "RIGHT", "UP", "DOWN", "PGUP", "PGDWN", "[", "]")
_FILTER_INPUT_CHARS = list("abcdefghijklmnopqrstuvwxyz0123456789")
_YEAR_INPUT_CHARS = list("0123456789")
_YEAR_INPUT_MAX_DIGITS = 4
_DEFAULT_CANVAS_WIDTH = 1920
_DEFAULT_CANVAS_HEIGHT = 1080
_OSD_SIZE_WAIT_SECONDS = 2.0
_OSD_SIZE_POLL_INTERVAL = 0.05
_SCHEDULE_POLL_SECONDS = 15.0
_RECONNECT_DELAYS_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)  # last value repeats past this many attempts
_RECONNECT_MAX_ATTEMPTS = len(_RECONNECT_DELAYS_SECONDS)
_RECONNECT_STABLE_SECONDS = 30.0  # uninterrupted playback this long after a reconnect resets the backoff to attempt 1
_PLEX_OVERLAY_ID = 9
_PLEX_SEARCH_OVERLAY_ID = 10
_PLEX_YEAR_OVERLAY_ID = 14
_PLEX_ITEM_MENU_OVERLAY_ID = 15
# A distinct id from the default 0 every other show_vod_info_overlay call
# site uses, so a "selected item" details popup (shown while merely
# browsing) never collides with a real "now playing" one -- both can, at
# least in principle, be triggered from related code paths.
_PLEX_SELECTED_ITEM_DETAILS_OVERLAY_ID = 16
# Movie/episode get all three entries; a show has no single file of its
# own to play from start, so it's just the two watched/unwatched ones --
# see _plex_item_menu_entries.
_PLEX_ITEM_MENU_KINDS = ("movie", "show", "episode")
_PLEX_MAX_ROWS = 8  # kept in sync with render_and_show_plex's max_rows, like _GUIDE_MAX_ROWS
# Kept in sync with overlay.py's own _PLEX_GRID_COLUMNS/_PLEX_GRID_ROWS --
# used here to size UP/DOWN/PGUP/PGDWN's steps while in grid view (see
# plex_move_up/plex_move_down/plex_move_page_up/plex_move_page_down).
_PLEX_GRID_COLUMNS = 6
_PLEX_GRID_ROWS = 3
# A show is favorited as a whole, not per-season/episode -- nothing finer-
# grained than this is ever a valid favorites target (see toggle_plex_favorite).
_PLEX_FAVORITABLE_KINDS = ("movie", "show")


@dataclass
class _PlexNavFrame:
    """One level of cli.py's Plex browser nav stack -- pushed by drilling
    into a container node (a library, show, or season), popped by ESC.
    The browser closes once the last frame is popped."""

    breadcrumb: str
    nodes: list[PlexNode]
    selected_index: int = 0


_CHROMECAST_OVERLAY_ID = 12
_CHROMECAST_MAX_ROWS = 8  # kept in sync with render_and_show_chromecast's max_rows, like _GUIDE_MAX_ROWS


@dataclass
class ActiveCast:
    """The one cast session that can be active at a time -- casting is
    generic to "whatever's currently playing", independent of whether
    that's a live channel, a VOD item, or a Plex item, so this lives
    outside both the channel and Plex sibling blocks. `cast` is the live
    pychromecast.Chromecast object, opaque outside chromecast.py."""

    device_name: str
    cast: object


_UPDATE_OVERLAY_ID = 13

# None = automatic (the container/stream's own aspect ratio); cycled with 'z'.
_ASPECT_RATIOS: list[tuple[str | None, str]] = [
    (None, "Auto"),
    ("4:3", "4:3"),
    ("16:9", "16:9"),
    ("2.35:1", "2.35:1 (Cinematic)"),
    ("1:1", "1:1"),
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


def _resolve_canvas_size(player: Player) -> tuple[int, int]:
    """The real (width, height) window/OSD size, waited for briefly so the
    very first overlay (shown right after playback starts, before mpv has
    decoded a frame) isn't sized against a guess -- which previously made
    it look oversized compared to the correctly-sized overlay shown on a
    later 'i' press."""
    deadline = time.monotonic() + _OSD_SIZE_WAIT_SECONDS
    while time.monotonic() < deadline:
        osd_size = player.osd_size()
        if osd_size:
            return osd_size
        time.sleep(_OSD_SIZE_POLL_INTERVAL)
    osd_size = player.osd_size()
    return osd_size or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)


def _resolve_canvas_width(player: Player) -> int:
    """The width-only counterpart to _resolve_canvas_size, for the many
    callers (every banner-style overlay except the guide's live-channel
    hero) that only ever size against canvas_width."""
    return _resolve_canvas_size(player)[0]


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


_EPG_PROGRESS_PRINT_INTERVAL_SECONDS = 2.0
# Slightly longer than the print interval above so a fresh OSD message
# always lands before the previous one's duration expires -- otherwise
# the "Loading EPG..." text would flicker off for a moment between
# each throttled update.
_EPG_PROGRESS_OSD_DURATION_MS = int(_EPG_PROGRESS_PRINT_INTERVAL_SECONDS * 1000) + 1000


def _make_epg_progress_reporter(
    label: str, on_message: Callable[[str], None] | None = None
) -> Callable[[int, int | None], None]:
    """A throttled on_progress callback for load_epg_for_playlist -- prints
    a running "Loading <label>... (N MB downloaded)" (or a percentage, if
    the server sent a Content-Length -- see epg._fetch_bytes) to stderr at
    most once every _EPG_PROGRESS_PRINT_INTERVAL_SECONDS, so a large
    feed's multi-minute download doesn't look like a hung terminal partway
    through. Each call to this factory gets its own independent throttle
    state, so the --list synchronous path and play_stream's background
    loader (each of which calls this once) never skip each other's
    updates by sharing one clock.

    `on_message`, if given, is also called with the same formatted text at
    the same throttled cadence -- play_stream uses this to mirror the
    message onto the player's own on-screen OSD (see epg_loader below),
    so it doesn't look like nothing's happening for anyone watching the
    video rather than the terminal."""
    last_printed = 0.0

    def report(downloaded: int, total: int | None) -> None:
        nonlocal last_printed
        now = time.monotonic()
        if now - last_printed < _EPG_PROGRESS_PRINT_INTERVAL_SECONDS:
            return
        last_printed = now
        downloaded_mb = downloaded / (1024 * 1024)
        if total:
            percent = min(100, round(downloaded / total * 100))
            message = f"Loading {label}... ({downloaded_mb:.0f} MB / {total / (1024 * 1024):.0f} MB, {percent}%)"
        else:
            message = f"Loading {label}... ({downloaded_mb:.0f} MB downloaded)"
        print(message, file=sys.stderr)
        if on_message is not None:
            on_message(message)

    return report


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


def hd_first(channels: list[Channel]) -> list[Channel]:
    """A stable sort putting HD channels (see Channel.is_hd) first, each
    group otherwise keeping its original relative order -- used both for
    the guide's browsing order and to pick the default channel on launch,
    so the two stay consistent (the channel a bare `tvdinner <url>` starts
    on is the same one the guide now shows first)."""
    return sorted(channels, key=lambda c: not c.is_hd)


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
    vod_items: list[VodItem] | None = None,
    epg: Epg | None = None,
    epg_loader: Callable[[Callable[[str], None] | None], Epg | None] | None = None,
    online_logos_loader: Callable[[], OnlineLogoIndex] | None = None,
    tmdb_api_token: str | None = None,
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
    plex_creds: PlexCreds | None = None,
    plex_root_nodes: list[PlexNode] | None = None,
    plex_client_id: str | None = None,
    plex_activity_reporting: bool = True,
    update_checker: Callable[[], UpdateInfo | None] | None = None,
    initial_vod_item: VodItem | None = None,
    vod_metadata_loader: Callable[[], VodItem | None] | None = None,
    full_screen: bool = True,
    glsl_shader: list[str] | None = None,
    interpolation: bool = False,
    playlist_source: str | None = None,
    history_path: Path | None = None,
) -> int:
    mpv_options = live_buffer_mpv_options(live_buffer_minutes)
    if glsl_shader:
        # mpv's own list-option separator (colon on Linux/macOS, semicolon
        # on Windows) -- os.pathsep matches it exactly on every platform
        # tvdinner ships for.
        mpv_options["glsl_shaders"] = os.pathsep.join(glsl_shader)
    if interpolation:
        # interpolation alone is a no-op -- mpv only actually interpolates
        # once video-sync switches from its default (audio) to
        # display-resample, which paces playback off the display's own
        # refresh rate instead of the audio clock.
        mpv_options["interpolation"] = True
        mpv_options["video_sync"] = "display-resample"
    player = Player(fullscreen=full_screen, **mpv_options)
    hide_timer: threading.Timer | None = None
    resize_timer: threading.Timer | None = None
    guide_logo_refresh_timer: threading.Timer | None = None
    history_image_refresh_timer: threading.Timer | None = None
    plex_image_refresh_timer: threading.Timer | None = None
    last_mouse_trigger = float("-inf")
    guide_visible = False
    guide_window_start: datetime | None = None
    selected_channel_url: str | None = None
    # The channel switch_to_channel is about to switch *away* from, so
    # 'b' (see switch_to_last_channel) can jump straight back to it --
    # repeated presses naturally toggle between the two, since each
    # switch (however it's triggered: the guide, or 'b' itself) records
    # whatever was playing right before it.
    last_channel: Channel | None = None
    details_visible = False
    details_channel: Channel | None = None
    details_programme: Programme | None = None
    aspect_index = 0
    pip_active = False
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
    vod_visible = False
    vod_list: list[VodItem] = list(vod_items) if vod_items else []
    vod_selected_index = 0
    history_browser_visible = False
    history_browser_list: list[HistoryEntry] = []
    history_browser_selected_index = 0
    # Seeded from initial_vod_item for a local-file launch (main()'s
    # local-video-file branch), which has no browser to select one from --
    # everything else here (resume, 'i' overlay, reconnect) treats it
    # exactly like any other VOD item once set, regardless of where it
    # came from.
    playing_vod_item: VodItem | None = initial_vod_item
    # The watch currently being timed for history.jsonl (see
    # _start_history_entry/_end_current_history_entry below) -- None
    # whenever nothing's being tracked (history disabled, or between
    # _end_current_history_entry clearing it and the next
    # _start_history_entry).
    history_kind: HistoryKind | None = None
    history_title: str | None = None
    history_url: str | None = None
    history_started_at: datetime | None = None
    about_visible = False
    online_logos: OnlineLogoIndex = EMPTY_LOGO_INDEX
    reconnect_attempt = 0
    reconnect_timer: threading.Timer | None = None
    reconnect_stability_timer: threading.Timer | None = None
    plex_visible = False
    plex_nav_stack: list[_PlexNavFrame] = []
    plex_favorites_only = False
    # Persists for the whole session (not reset per nav-stack level) --
    # same "toggle once, stays until toggled back" persistence as
    # plex_favorites_only itself, per the user's own requirement for this.
    plex_grid_view = True
    # A fresh id per distinct Plex item played (see select_plex_node),
    # not per report -- report_plex_timeline needs the same session id
    # across repeated calls for one item so Plex treats them as one
    # ongoing session rather than a new one starting each time.
    plex_playback_session_id: str | None = None
    # The last position/duration _report_plex_state actually managed to
    # read -- see its own comment on why the final shutdown report needs
    # this fallback.
    plex_last_known_position: tuple[float, float] | None = None
    # Set whenever an overlay (help/about/chromecast/history/update notice)
    # closes the Plex browser to make room for itself -- unlike the guide,
    # which always has live video to fall back on, a Plex session with
    # nothing playing has nothing else to show once that overlay closes,
    # so its own close_X() reopens the browser if this is set. A single
    # shared flag (rather than one per overlay) still behaves correctly
    # across a chain of overlays stealing focus from each other: each
    # hop's own "close whatever else is open" preamble re-triggers and
    # re-sets it via the same close_plex_browser() call below, all before
    # anything is actually rendered to the screen.
    plex_reopen_pending = False
    plex_search_input_active = False
    plex_search_text = ""
    plex_year_input_active = False
    plex_year_text = ""
    # The node the item menu (hold ENTER) was opened on, and which of its
    # entries is currently highlighted -- None whenever the menu is
    # closed, which doubles as the "is it open" check every handler below
    # guards on, same as plex_nav_stack itself doubling as plex_visible's
    # underlying data.
    plex_item_menu_node: PlexNode | None = None
    plex_item_menu_index = 0
    chromecast_visible = False
    chromecast_devices: list[CastDevice] = []
    chromecast_selected_index = 0
    chromecast_scanning = False
    chromecast_stop_discovery: Callable[[], None] | None = None
    active_cast: ActiveCast | None = None
    available_update: UpdateInfo | None = None
    update_notice_visible = False

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

    def cancel_guide_logo_refresh_timer() -> None:
        nonlocal guide_logo_refresh_timer
        if guide_logo_refresh_timer is not None:
            guide_logo_refresh_timer.cancel()
            guide_logo_refresh_timer = None

    def cancel_history_image_refresh_timer() -> None:
        nonlocal history_image_refresh_timer
        if history_image_refresh_timer is not None:
            history_image_refresh_timer.cancel()
            history_image_refresh_timer = None

    def cancel_plex_image_refresh_timer() -> None:
        nonlocal plex_image_refresh_timer
        if plex_image_refresh_timer is not None:
            plex_image_refresh_timer.cancel()
            plex_image_refresh_timer = None

    def cancel_reconnect_timer() -> None:
        nonlocal reconnect_timer
        if reconnect_timer is not None:
            reconnect_timer.cancel()
            reconnect_timer = None

    def cancel_reconnect_stability_timer() -> None:
        nonlocal reconnect_stability_timer
        if reconnect_stability_timer is not None:
            reconnect_stability_timer.cancel()
            reconnect_stability_timer = None

    def _reset_reconnect_state() -> None:
        # Called whenever playback changes deliberately (a manual channel/
        # recording/VOD switch) -- any reconnect attempt or stability timer
        # in flight belonged to whatever was playing before and would
        # otherwise fire later against the new target.
        nonlocal reconnect_attempt
        cancel_reconnect_timer()
        cancel_reconnect_stability_timer()
        reconnect_attempt = 0

    def cycle_aspect_ratio() -> None:
        nonlocal aspect_index
        aspect_index = (aspect_index + 1) % len(_ASPECT_RATIOS)
        ratio, label = _ASPECT_RATIOS[aspect_index]
        player.set_video_aspect(ratio)
        player.show_text(f"Aspect ratio: {label}", duration_ms=2000)
        logger.info("Aspect ratio -> %s", label)

    def toggle_picture_in_picture() -> None:
        nonlocal pip_active
        pip_active = not pip_active
        if pip_active:
            # A small, always-on-top corner window isn't meant to be read
            # at guide-grid detail -- close whatever's open first, same as
            # toggle_help_overlay/toggle_recordings_browser/etc. already
            # close each other.
            if guide_visible:
                close_guide()
            if recordings_visible:
                close_recordings_browser()
            if schedule_browser_visible:
                close_schedule_browser()
            if help_visible:
                close_help_overlay()
            if vod_visible:
                close_vod_browser()
            if about_visible:
                close_about_overlay()
        player.set_picture_in_picture(pip_active)
        player.show_text("Picture-in-picture: On" if pip_active else "Picture-in-picture: Off", duration_ms=2000)
        logger.info("Picture-in-picture -> %s", "on" if pip_active else "off")

    def toggle_subtitles() -> None:
        # Live TV subtitle availability depends entirely on the stream
        # itself (e.g. UK DVB broadcasts commonly carry one), so this
        # queries the player fresh each press rather than tracking its own
        # on/off state -- mpv already knows whether a track is selected
        # and visible, and a channel switch can change stream availability
        # underneath without this function ever being told.
        if not player.has_subtitle_track:
            player.show_text("No subtitles available for this channel", duration_ms=3000)
            return
        enabled = not player.subtitles_enabled
        player.set_subtitles_enabled(enabled)
        player.show_text("Subtitles: On" if enabled else "Subtitles: Off", duration_ms=2000)
        logger.info("Subtitles -> %s", "on" if enabled else "off")

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

        # playing_vod_item.title (rather than the closed-over `title`
        # param) so a session that starts with no known title at all --
        # e.g. a YouTube URL, whose real title only arrives later via a
        # background oEmbed lookup -- still gets a meaningful recording
        # filename instead of the generic "stream" fallback.
        if channel is not None:
            label = channel.name
        elif playing_vod_item is not None:
            label = playing_vod_item.title
        else:
            label = title or "stream"
        recording_path = target_dir / recording_filename(label, datetime.now())
        player.start_recording(str(recording_path))
        player.show_text(f"Recording to {recording_path.name}", duration_ms=3000)
        logger.info("Recording started: %s", recording_path)

    def _reopen_plex_if_pending() -> None:
        # Shared by every overlay's close_X() below that might have
        # stolen focus from the Plex browser (see plex_reopen_pending's
        # own comment above) -- only ever actually reaches open_plex_browser
        # in a Plex session, since that's the only session type that can
        # ever set the flag in the first place.
        nonlocal plex_reopen_pending
        if plex_reopen_pending:
            plex_reopen_pending = False
            open_plex_browser()

    def close_help_overlay() -> None:
        nonlocal help_visible
        if not help_visible:
            return
        player.clear_overlay(overlay_id=_HELP_OVERLAY_ID)
        player.unbind_key("ESC")
        help_visible = False
        logger.info("Help overlay closed")
        _reopen_plex_if_pending()

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
        nonlocal plex_reopen_pending
        # '?' isn't one of the a-z/0-9 keys the guide filter's (or Plex
        # search's) text-entry shadows (see _FILTER_INPUT_CHARS), so it
        # stays bound while typing -- guard here instead, rather than
        # interrupting that to open/close an unrelated overlay.
        if filter_input_active or plex_search_input_active:
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
        if vod_visible:
            close_vod_browser()
        if about_visible:
            close_about_overlay()
        if plex_visible:
            close_plex_browser()
            plex_reopen_pending = True
        if chromecast_visible:
            close_chromecast_picker()
        if update_notice_visible:
            close_update_notice()
        if history_browser_visible:
            close_history_browser()
        open_help_overlay()

    def close_about_overlay() -> None:
        nonlocal about_visible
        if not about_visible:
            return
        player.clear_overlay(overlay_id=_ABOUT_OVERLAY_ID)
        player.unbind_key("ESC")
        about_visible = False
        logger.info("About overlay closed")
        _reopen_plex_if_pending()

    def open_about_overlay() -> None:
        nonlocal about_visible
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_about_overlay(__version__, osd_size[0], osd_size[1])
        x = (osd_size[0] - image.width) // 2
        y = (osd_size[1] - image.height) // 2
        player.show_overlay(image, x=x, y=y, overlay_id=_ABOUT_OVERLAY_ID)
        player.on_key_press("ESC", close_about_overlay)
        about_visible = True
        logger.info("About overlay opened")

    def toggle_about_overlay() -> None:
        nonlocal plex_reopen_pending
        if about_visible:
            close_about_overlay()
            return
        if guide_visible:
            close_guide()
        if recordings_visible:
            close_recordings_browser()
        if schedule_browser_visible:
            close_schedule_browser()
        if vod_visible:
            close_vod_browser()
        if help_visible:
            close_help_overlay()
        if plex_visible:
            close_plex_browser()
            plex_reopen_pending = True
        if chromecast_visible:
            close_chromecast_picker()
        if update_notice_visible:
            close_update_notice()
        if history_browser_visible:
            close_history_browser()
        open_about_overlay()

    def _render_history_from_image_refresh_timer() -> None:
        # The timer's own target rather than render_and_show_history
        # directly, so history_browser_visible is rechecked right before
        # actually rendering -- same reasoning as
        # _render_guide_from_logo_refresh_timer above.
        nonlocal history_image_refresh_timer
        history_image_refresh_timer = None
        if history_browser_visible:
            render_and_show_history()

    def _on_history_image_resolved() -> None:
        # Runs on the resolving background thread (see
        # overlay.prefetch_images), potentially once per row on the
        # page -- debounced into a single re-render, same reasoning as
        # _on_guide_logo_resolved above.
        nonlocal history_image_refresh_timer
        if not history_browser_visible:
            return
        cancel_history_image_refresh_timer()
        history_image_refresh_timer = threading.Timer(
            _GUIDE_LOGO_REFRESH_DEBOUNCE_SECONDS, _render_history_from_image_refresh_timer
        )
        history_image_refresh_timer.daemon = True
        history_image_refresh_timer.start()

    def close_history_browser() -> None:
        nonlocal history_browser_visible, history_browser_selected_index
        if not history_browser_visible:
            return
        cancel_history_image_refresh_timer()
        player.clear_overlay(overlay_id=_HISTORY_OVERLAY_ID)
        player.unbind_key("UP")
        player.unbind_key("DOWN")
        player.unbind_key("PGUP")
        player.unbind_key("PGDWN")
        player.unbind_key("ENTER")
        player.unbind_key("KP_ENTER")
        player.unbind_key("ESC")
        # Unlike the guide/VOD/recordings/schedule browsers this otherwise
        # mirrors, the history browser is reachable from every session
        # type (Plex, VOD, local file, YouTube), not just a channel/EPG
        # one -- but toggle_live_pause (unlike show_epg_overlay, the old
        # base ENTER binding this replaced) is defined unconditionally at
        # the top of this function, so restoring it here needs no such
        # guard.
        player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
        history_browser_visible = False
        history_browser_selected_index = 0
        logger.info("History browser closed")
        _reopen_plex_if_pending()

    def render_and_show_history() -> bool:
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_history_browser(
            history_browser_list,
            history_browser_selected_index,
            osd_size[0],
            osd_size[1],
            max_rows=_HISTORY_MAX_ROWS,
        )
        if image is None:
            return False
        x = (osd_size[0] - image.width) // 2
        y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
        player.show_overlay(image, x=x, y=y, overlay_id=_HISTORY_OVERLAY_ID)

        # Never blocking: only spawns background fetches for thumbnails
        # not already cached/in-flight -- the image just rendered above
        # already used cached_image's cache-only read (falling back to a
        # placeholder for anything not yet resolved). Only the currently
        # visible page, same as render_and_show_guide's own channel-logo
        # prefetch -- paging re-triggers this for whatever's newly shown.
        visible = visible_history_entries(history_browser_list, history_browser_selected_index, max_rows=_HISTORY_MAX_ROWS)
        prefetch_images((entry.image_url for entry in visible), on_resolved=_on_history_image_resolved)
        return True

    def move_history_selection(step: int) -> None:
        nonlocal history_browser_selected_index
        if not history_browser_visible or not history_browser_list:
            return
        history_browser_selected_index = max(0, min(len(history_browser_list) - 1, history_browser_selected_index + step))
        render_and_show_history()

    def play_selected_history_entry() -> None:
        # ENTER replays the selected entry if (and only if) its own
        # source is what's currently loaded -- a channel needs the exact
        # playlist reloaded (its URL matched against the current
        # channel list, not just playlist_source, in case the same
        # playlist changed between then and now); a VOD item (including
        # Plex, since its stored url is already a complete, directly
        # playable file link, no re-resolution needed) needs the same
        # playlist_source; a recording needs nothing beyond the file
        # still existing on disk, since recordings were always playable
        # from any session already (see the 'w' recordings browser,
        # which has no such restriction of its own). Anything else just
        # closes the browser, same as ESC always has.
        nonlocal channel, playing_recording, playing_vod_item, last_channel
        if not history_browser_visible or not history_browser_list:
            return
        entry = history_browser_list[history_browser_selected_index]

        if entry.kind == "recording":
            path = Path(entry.url)
            if not path.is_file():
                close_history_browser()
                player.show_text(f"'{entry.title}' no longer exists", duration_ms=3000)
                return
            close_history_browser()
            _save_current_recording_position()
            _save_current_vod_position()
            _end_current_history_entry()
            _reset_reconnect_state()
            playing_vod_item = None
            try:
                size_bytes = path.stat().st_size
            except OSError:
                size_bytes = 0
            playing_recording = RecordingFile(path=path, label=entry.title, recorded_at=entry.started_at, size_bytes=size_bytes)
            resume_at = playback_positions.get(str(path))
            player.play(str(path), title=entry.title, start=resume_at)
            _start_history_entry("recording", entry.title, str(path))
            if resume_at:
                player.show_text(f"Resuming: {entry.title}", duration_ms=3000)
                logger.info("Resuming recording from history at %.0fs: %s", resume_at, path)
            else:
                player.show_text(f"Playing recording: {entry.title}", duration_ms=3000)
                logger.info("Replaying recording from history: %s", path)
            return

        if entry.playlist_source != playlist_source:
            close_history_browser()
            player.show_text(f"'{entry.title}' isn't from the current source", duration_ms=3000)
            return

        if entry.kind == "channel":
            matched = next((c for c in (channels or []) if c.url == entry.url), None)
            if matched is None or channel is None:
                close_history_browser()
                player.show_text(f"'{entry.title}' isn't in the current playlist", duration_ms=3000)
                return
            close_history_browser()
            switch_to_channel(matched)
            return

        if entry.kind == "vod":
            close_history_browser()
            _save_current_recording_position()
            _save_current_vod_position()
            _end_current_history_entry()
            _reset_reconnect_state()
            playing_recording = None
            item = VodItem(
                title=entry.title,
                url=entry.url,
                poster_url=entry.image_url,
                year=entry.year,
                rating=entry.rating,
                rating_is_tmdb=entry.rating_is_tmdb,
                director=entry.director,
            )
            playing_vod_item = item
            _enrich_vod_hero_art_in_background(item)
            resume_at = playback_positions.get(item.url)
            player.play(item.url, title=item.title, start=resume_at)
            _start_history_entry("vod", item.title, item.url)
            if resume_at:
                player.show_text(f"Resuming: {item.title}", duration_ms=3000)
                logger.info("Resuming VOD item from history at %.0fs: %s", resume_at, item.url)
            else:
                player.show_text(f"Playing: {item.title}", duration_ms=3000)
                logger.info("Replaying VOD item from history: %s", item.url)
            return

    def open_history_browser() -> None:
        nonlocal history_browser_visible, history_browser_list, history_browser_selected_index
        entries: list[HistoryEntry] = []
        if history_path is not None:
            entries, warnings = load_history(history_path)
            for warning in warnings:
                logger.warning(warning)
        if not entries:
            player.show_text("No watch history yet", duration_ms=3000)
            return

        entries.reverse()  # load_history returns oldest first; most recent first here
        history_browser_list = entries
        history_browser_selected_index = 0
        if render_and_show_history():
            history_browser_visible = True
            player.on_key_press("UP", lambda: move_history_selection(-1))
            player.on_key_press("DOWN", lambda: move_history_selection(1))
            player.on_key_press("PGUP", lambda: move_history_selection(-_HISTORY_MAX_ROWS))
            player.on_key_press("PGDWN", lambda: move_history_selection(_HISTORY_MAX_ROWS))
            # ENTER replays the selected entry if its source is currently
            # loaded (see play_selected_history_entry), otherwise just
            # closes the browser -- either way it never falls through to
            # the base ENTER binding (show_epg_overlay, in a channel
            # session only) still active underneath.
            player.on_key_press("ENTER", play_selected_history_entry)
            player.on_key_press("KP_ENTER", play_selected_history_entry)
            player.on_key_press("ESC", close_history_browser)
            logger.info("History browser opened (%d entries)", len(history_browser_list))

    def toggle_history_browser() -> None:
        nonlocal plex_reopen_pending
        if history_browser_visible:
            close_history_browser()
            return
        if guide_visible:
            close_guide()
        if recordings_visible:
            close_recordings_browser()
        if schedule_browser_visible:
            close_schedule_browser()
        if vod_visible:
            close_vod_browser()
        if help_visible:
            close_help_overlay()
        if about_visible:
            close_about_overlay()
        if plex_visible:
            close_plex_browser()
            plex_reopen_pending = True
        if chromecast_visible:
            close_chromecast_picker()
        if update_notice_visible:
            close_update_notice()
        open_history_browser()

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
        if active_cast is not None:
            # Local playback is deliberately paused for the duration of a
            # cast (see select_chromecast_device) -- 'p' un-pausing it here
            # would silently desync local mpv from the still-active cast
            # session. Disconnecting is the one sanctioned way back to
            # local playback.
            player.show_text("Casting -- disconnect first to resume local playback", duration_ms=3000)
            return
        if player.is_paused:
            player.set_paused(False)
            cancel_live_pause_timer()
            # Removes the "what are we watching" overlay a pause just
            # below shows immediately, rather than leaving it up until
            # its own hide_timer would otherwise get to it -- resuming
            # is a clear enough signal the user's done looking at it.
            # Safe unconditionally: this is the same default overlay
            # slot show_epg_overlay/show_vod_info_overlay always use
            # (id 0), distinct from the guide/browsers/prompts, which
            # all use their own ids -- clearing it here can never close
            # any of those.
            cancel_hide_timer()
            player.clear_overlay()
            player.show_text("Resumed", duration_ms=2000)
            logger.info("Playback resumed")
            _report_plex_state("playing")
            return

        player.set_paused(True)
        _report_plex_state("paused")
        if playing_recording is None and playing_vod_item is None:
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
            # A recording or VOD movie is already fully seekable with no
            # buffer to run out of -- a plain pause, no timer.
            player.show_text("Paused", duration_ms=2000)
        logger.info("Playback paused")
        # Show what's playing on pause, same as a manual 'i'/MENU press --
        # auto-hides itself after the usual _OVERLAY_HIDE_AFTER_SECONDS
        # (see show_epg_overlay/show_vod_info_overlay), leaving just the
        # paused frame behind. show_epg_overlay only exists in a channel/
        # EPG session (see the "if channel is not None and display is not
        # None:" guard around its own definition) -- everywhere else
        # (Plex, local file/YouTube), show_vod_info_overlay is the
        # equivalent already bound to 'i'/MENU directly.
        if channel is not None and display is not None:
            show_epg_overlay()
        elif playing_vod_item is not None:
            show_vod_info_overlay()

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

    def _save_current_vod_position() -> None:
        # Same as _save_current_recording_position, but for whatever VOD
        # movie is currently playing -- keyed by its stream URL rather than
        # a local file path (see playback_positions._still_valid, which
        # knows not to prune a remote URL key just because it isn't a
        # local file that exists on disk).
        if playing_vod_item is None:
            return
        position_and_duration = player.playback_position()
        if position_and_duration is None:
            return
        position, duration = position_and_duration
        key = playing_vod_item.url
        if position < _RESUME_MIN_SECONDS or (duration - position) < _RESUME_END_MARGIN_SECONDS:
            playback_positions.pop(key, None)
        else:
            playback_positions[key] = position
        try:
            save_playback_positions(playback_positions_path, playback_positions)
        except OSError as exc:
            logger.warning("Could not save playback position to %s: %s", playback_positions_path, exc)

    def _report_plex_state(state: str, *, background: bool = True) -> None:
        # No-op unless this is an actual Plex-sourced item currently
        # playing and --no-plex-activity wasn't passed -- every other
        # VodItem source (Xtream/Stalker/M3U/local-file/YouTube) leaves
        # rating_key unset, so this never fires for them. See
        # plex.report_plex_timeline's own docstring for what this makes
        # visible (Plex's dashboard, Tautulli, etc.) and updates
        # (viewCount/viewOffset) on Plex's side.
        nonlocal plex_last_known_position
        if (
            not plex_activity_reporting
            or plex_creds is None
            or plex_client_id is None
            or plex_playback_session_id is None
            or playing_vod_item is None
            or playing_vod_item.rating_key is None
        ):
            return
        position_and_duration = player.playback_position()
        if position_and_duration is not None:
            plex_last_known_position = position_and_duration
        elif state == "stopped" and plex_last_known_position is not None:
            # Confirmed live: wait_for_playback() (and thus the top-level
            # finally block's own "stopped" report) only ever returns
            # once mpv's core has already shut down, at which point
            # playback_position() always returns None -- every quit path
            # (BS, mpv's own default 'q', the window close button) hits
            # this, not just a rare edge case. Falling back to the last
            # position a report actually managed to read means the final
            # "stopped" call still finalizes Plex's own watched state
            # correctly instead of silently never firing and leaving a
            # stale "still playing" session in Plex's dashboard/Tautulli
            # until its own timeout eventually expires it.
            position_and_duration = plex_last_known_position
        else:
            return
        position, duration = position_and_duration
        # Captured now, not re-read inside _send() below -- playing_vod_item/
        # plex_playback_session_id can change (a rapid item switch) before
        # a backgrounded report actually runs, and this report describes
        # whatever was current when it was requested, not whatever happens
        # to be current by the time the thread gets scheduled.
        rating_key = playing_vod_item.rating_key
        session_id = plex_playback_session_id

        def _send() -> None:
            ok, error = report_plex_timeline(
                plex_creds,
                client_id=plex_client_id,
                session_id=session_id,
                rating_key=rating_key,
                state=state,
                position_seconds=position,
                duration_seconds=duration,
            )
            if not ok:
                logger.warning("Plex timeline report failed: %s", error)

        if background:
            # Every call site except the final shutdown report below --
            # never blocks a keypress or the render loop on a slow/
            # unreachable Plex server.
            threading.Thread(target=_send, daemon=True).start()
        else:
            _send()

    def _start_history_entry(kind: HistoryKind, title: str, url: str) -> None:
        # Called once for the very first thing played, and again right
        # after every subsequent player.play() at a genuine "switch to
        # different content" call site (channel switch, VOD/recording/Plex
        # selection) -- never on a reconnect, which replays the same
        # content after a network drop rather than starting something new
        # (see handle_playback_error's _attempt_reconnect, which calls
        # player.play() directly rather than through here). Always pairs
        # with a preceding _end_current_history_entry() call at the same
        # call site, so at most one watch is ever being timed at once.
        nonlocal history_kind, history_title, history_url, history_started_at
        if history_path is None:
            return
        history_kind = kind
        history_title = title
        history_url = url
        history_started_at = datetime.now(timezone.utc)

    def _end_current_history_entry() -> None:
        # Finalizes whatever _start_history_entry started, if anything --
        # a no-op if history is disabled or nothing's being tracked (e.g.
        # two calls in a row with no intervening _start_history_entry).
        # Best-effort like _save_current_recording_position/
        # _save_current_vod_position above: a write failure here is logged
        # and swallowed rather than interrupting playback over what's
        # deliberately just-in-case data.
        #
        # Cover art/rating/director (for the 'x' history browser) are
        # read from the *current* channel/playing_vod_item here, at close
        # time, rather than captured at _start_history_entry's open time
        # -- a VOD item's poster/rating/director are filled in later, by
        # a background TMDB/oEmbed lookup (see vod_metadata_loader), and
        # by the time a watch actually ends that lookup has almost always
        # already landed. channel/playing_vod_item are guaranteed to
        # still refer to whatever's being switched *away from* here: every
        # call site calls this before reassigning either nonlocal.
        nonlocal history_kind, history_title, history_url, history_started_at
        if history_path is None or history_started_at is None:
            return
        title = history_title
        channel_name: str | None = None
        image_url: str | None = None
        year: str | None = None
        rating: str | None = None
        rating_is_tmdb = False
        director: str | None = None
        if history_kind == "channel" and channel is not None:
            # history_title (set at _start_history_entry time) is just
            # the channel's own name -- swapped out here for whatever
            # programme the EPG says was actually airing when the watch
            # *started* (not close time: a long watch could span a
            # programme change, and "what did you tune into" is the more
            # useful answer than "what happened to be on when you left").
            # No token/network lookup needed the way VOD's TMDB fallback
            # does -- the EPG feed is already fully loaded well before
            # any mid-session channel switch, so this is a pure, already-
            # cached read, safe to do inline here.
            channel_name = channel.name
            image_url = channel.tvg_logo
            programme, _ = current_and_next_programmes(channel, epg, display, history_started_at)
            if programme is not None:
                title = programme.title
                image_url = programme.poster_url or image_url
                year = programme.year
                director = programme.director
        elif history_kind == "vod" and playing_vod_item is not None:
            image_url = playing_vod_item.poster_url
            year = playing_vod_item.year
            rating = playing_vod_item.rating
            rating_is_tmdb = playing_vod_item.rating_is_tmdb
            director = playing_vod_item.director
        elif history_kind == "recording":
            # No poster of any kind exists for a local recording -- this
            # resolves lazily (see overlay.recording_thumbnail_url/
            # _recording_thumbnail) to an actual frame captured from the
            # file itself, the first time the history browser needs it,
            # not here (grabbing a frame takes a moment; this runs on
            # whatever's switching away from the recording, which must
            # not stall).
            image_url = recording_thumbnail_url(Path(history_url))
        entry = HistoryEntry(
            kind=history_kind,
            title=title,
            url=history_url,
            playlist_source=playlist_source,
            started_at=history_started_at,
            ended_at=datetime.now(timezone.utc),
            channel_name=channel_name,
            image_url=image_url,
            year=year,
            rating=rating,
            rating_is_tmdb=rating_is_tmdb,
            director=director,
        )
        history_kind = None
        history_title = None
        history_url = None
        history_started_at = None
        try:
            append_history_entry(history_path, entry)
        except OSError as exc:
            logger.warning("Could not append history entry to %s: %s", history_path, exc)

    def show_vod_info_overlay() -> None:
        # A centered "now playing" popup (poster, synopsis, progress) for
        # whatever VOD item is currently playing -- Plex populates poster/
        # rating/description itself (see resolve_plex_playable), a local
        # file gets them from a background TMDB lookup (see main()'s
        # local-video-file branch), and this works for any VodItem
        # regardless, poster-less or not. Shared between the Plex
        # browser's own 'i' binding, the local-file session's own 'i'
        # binding, and the channel session's show_epg_overlay (which
        # falls through to this when playing_vod_item is set, the same
        # way it already does for playing_recording).
        #
        # While the Plex browser is open and the current selection is a
        # movie/episode (node.container is False for those, see
        # PlexNode.container), this shows details for *that* selection
        # instead -- resolved fresh via resolve_plex_playable, same as
        # select_plex_node/the item menu's "Play from Start", but without
        # starting playback or touching playing_vod_item. A show/season/
        # library row has no single file to resolve like this, so it
        # falls through to the plain "currently playing" behavior below,
        # same as everywhere else this function is used. Forces the plain
        # card layout (prefer_card=True) rather than the full-bleed hero
        # this function uses everywhere else -- this popup already sits
        # on top of the browser's own full-screen poster backdrop, and a
        # second, different backdrop stacked on top of that read as
        # cluttered (confirmed live).
        nonlocal hide_timer
        if plex_visible and plex_nav_stack:
            frame = plex_nav_stack[-1]
            nodes = plex_frame_nodes(frame)
            if nodes:
                node = nodes[frame.selected_index]
                if not node.container:
                    player.show_text("Loading...", duration_ms=2000)
                    item, error = resolve_plex_playable(plex_creds, node)
                    if item is None:
                        player.show_text(f"Plex error: {error}", duration_ms=4000)
                        logger.error("Plex error resolving '%s': %s", node.title, error)
                        return
                    cancel_hide_timer()
                    osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                    image = render_vod_info_overlay(item, osd_size[0], osd_size[1], eyebrow="DETAILS", prefer_card=True)
                    x = (osd_size[0] - image.width) // 2
                    y = (osd_size[1] - image.height) // 2
                    player.show_overlay(image, x=x, y=y, overlay_id=_PLEX_SELECTED_ITEM_DETAILS_OVERLAY_ID)
                    hide_timer = threading.Timer(
                        _OVERLAY_HIDE_AFTER_SECONDS,
                        lambda: player.clear_overlay(overlay_id=_PLEX_SELECTED_ITEM_DETAILS_OVERLAY_ID),
                    )
                    hide_timer.daemon = True
                    hide_timer.start()
                    return
        if playing_vod_item is None:
            player.show_text("Nothing playing yet", duration_ms=2000)
            return
        cancel_hide_timer()
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        position, duration = player.playback_position() or (None, None)
        image = render_vod_info_overlay(
            playing_vod_item, osd_size[0], osd_size[1], position_seconds=position, duration_seconds=duration
        )
        x = (osd_size[0] - image.width) // 2
        y = (osd_size[1] - image.height) // 2
        player.show_overlay(image, x=x, y=y)
        hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
        hide_timer.daemon = True
        hide_timer.start()

    def _current_playable() -> tuple[str, str, bool] | None:
        # (url, title, is_live) for whatever's currently playing, or None
        # if there's nothing to cast yet -- mirrors how
        # select_plex_node/switch_to_channel already pick a url/title
        # pair, just generalized across both session types since casting
        # applies to either one.
        if playing_vod_item is not None:
            return playing_vod_item.url, playing_vod_item.title, False
        if channel is not None:
            return channel.url, channel.name, True
        return None

    def _stop_active_cast_session() -> None:
        if active_cast is None:
            return
        stop_casting(active_cast.cast)

    def close_chromecast_picker() -> None:
        nonlocal chromecast_visible, chromecast_stop_discovery
        if not chromecast_visible:
            return
        player.clear_overlay(overlay_id=_CHROMECAST_OVERLAY_ID)
        for key in ("UP", "DOWN", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC"):
            player.unbind_key(key)
        player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
        if chromecast_stop_discovery is not None:
            chromecast_stop_discovery()
            chromecast_stop_discovery = None
        chromecast_visible = False
        logger.info("Chromecast picker closed")
        _reopen_plex_if_pending()

    def render_and_show_chromecast() -> None:
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_cast_picker(
            "Chromecast",
            chromecast_devices,
            chromecast_selected_index,
            active_cast.device_name if active_cast is not None else None,
            chromecast_scanning,
            osd_size[0],
            osd_size[1],
            max_rows=_CHROMECAST_MAX_ROWS,
        )
        x = (osd_size[0] - image.width) // 2
        y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
        player.show_overlay(image, x=x, y=y, overlay_id=_CHROMECAST_OVERLAY_ID)

    def _chromecast_row_count() -> int:
        return len(chromecast_devices) + (1 if active_cast is not None else 0)

    def move_chromecast_selection(step: int) -> None:
        nonlocal chromecast_selected_index
        if not chromecast_visible:
            return
        total = _chromecast_row_count()
        if total == 0:
            return
        chromecast_selected_index = max(0, min(total - 1, chromecast_selected_index + step))
        render_and_show_chromecast()

    def select_chromecast_device() -> None:
        nonlocal active_cast, chromecast_visible
        if not chromecast_visible or _chromecast_row_count() == 0:
            return

        if active_cast is not None and chromecast_selected_index == 0:
            # The synthetic "Disconnect" row -- see render_cast_picker's
            # "disconnect row first, if present" indexing convention.
            # stop_casting() before close_chromecast_picker(), matching
            # the connect path below, even though this one doesn't need
            # zeroconf (it's operating on an already-established
            # connection) -- keeping the ordering consistent either way.
            device_name = active_cast.device_name
            try:
                _stop_active_cast_session()
            except Exception as exc:
                logger.warning("Error stopping cast to %s (continuing anyway): %s", device_name, exc)
            close_chromecast_picker()
            active_cast = None
            player.set_paused(False)
            player.show_text(f"Disconnected from {device_name}", duration_ms=3000)
            logger.info("Stopped casting to %s", device_name)
            return

        device_index = chromecast_selected_index - (1 if active_cast is not None else 0)
        device = chromecast_devices[device_index]

        playable = _current_playable()
        if playable is None:
            player.show_text("Nothing to cast yet", duration_ms=2000)
            return
        url, title, is_live = playable

        # cast_url() must run *before* close_chromecast_picker() (which
        # stops discovery) -- confirmed live that pychromecast's connect
        # step needs the same still-running zeroconf instance discovery
        # uses to resolve the device's host, and closing the picker (and
        # so stopping discovery) first makes the connection attempt throw.
        player.show_text(f"Connecting to {device.name}...", duration_ms=3000)
        try:
            cast_url(device.cast, url, title, is_live)
        except Exception as exc:
            close_chromecast_picker()
            player.show_text(f"Could not cast to {device.name}: {exc}", duration_ms=4000)
            logger.error("Could not cast to %s: %s", device.name, exc)
            return
        close_chromecast_picker()

        # A manual live-TV pause from before this cast started must not
        # be allowed to auto-resume mid-cast (see cancel_live_pause_timer)
        # -- a cast session is expected to run far longer than the
        # buffer-limited local pause it's piggybacking on.
        cancel_live_pause_timer()
        player.set_paused(True)
        active_cast = ActiveCast(device_name=device.name, cast=device.cast)
        player.show_text(f"Casting to {device.name}", duration_ms=3000)
        logger.info("Casting '%s' to %s", title, device.name)

    def open_chromecast_picker() -> None:
        nonlocal chromecast_visible, chromecast_devices, chromecast_selected_index, chromecast_scanning, chromecast_stop_discovery
        nonlocal plex_reopen_pending
        if not chromecast_available():
            player.show_text("Chromecast support not installed (pip install tvdinner[chromecast])", duration_ms=4000)
            return
        # Mutual exclusivity with every other overlay, matching every
        # other toggle_X -- each close_X() here is only ever actually
        # reached if its own X_visible flag is set, which only channel or
        # Plex sessions (whichever defined that closure) can ever set, so
        # this is safe to call unconditionally from a shared top-level
        # closure like this one.
        if guide_visible:
            close_guide()
        if recordings_visible:
            close_recordings_browser()
        if schedule_browser_visible:
            close_schedule_browser()
        if vod_visible:
            close_vod_browser()
        if help_visible:
            close_help_overlay()
        if about_visible:
            close_about_overlay()
        if plex_visible:
            close_plex_browser()
            plex_reopen_pending = True
        if update_notice_visible:
            close_update_notice()
        chromecast_devices = []
        chromecast_selected_index = 0
        chromecast_scanning = True
        chromecast_visible = True
        render_and_show_chromecast()
        player.on_key_press("UP", lambda: move_chromecast_selection(-1))
        player.on_key_press("DOWN", lambda: move_chromecast_selection(1))
        player.on_key_press("PGUP", lambda: move_chromecast_selection(-_CHROMECAST_MAX_ROWS))
        player.on_key_press("PGDWN", lambda: move_chromecast_selection(_CHROMECAST_MAX_ROWS))
        player.on_key_press("ENTER", select_chromecast_device)
        player.on_key_press("KP_ENTER", select_chromecast_device)
        player.on_key_press("ESC", close_chromecast_picker)

        def _on_device_found(cast) -> None:
            nonlocal chromecast_scanning
            if not chromecast_visible:
                return  # picker already closed -- discard, same race guard the EPG/logo background loaders use
            if any(d.cast.uuid == cast.uuid for d in chromecast_devices):
                return  # zeroconf can re-announce the same device
            chromecast_devices.append(CastDevice(name=cast.name, cast=cast))
            chromecast_scanning = False
            render_and_show_chromecast()

        chromecast_stop_discovery = discover_chromecasts(_on_device_found)
        logger.info("Chromecast picker opened")

    def toggle_chromecast_picker() -> None:
        if chromecast_visible:
            close_chromecast_picker()
            return
        open_chromecast_picker()

    def close_update_notice() -> None:
        nonlocal update_notice_visible
        if not update_notice_visible:
            return
        player.clear_overlay(overlay_id=_UPDATE_OVERLAY_ID)
        player.unbind_key("y")
        player.unbind_key("n")
        player.unbind_key("ESC")
        update_notice_visible = False
        logger.info("Update notice closed")
        _reopen_plex_if_pending()

    def _mark_update_skipped() -> None:
        # Persisted so this exact version isn't shown again on a future
        # launch -- a genuinely newer release still notifies normally,
        # since check_for_update always compares against the latest tag,
        # not just "is there anything unskipped".
        if available_update is None:
            return
        state, warnings = load_update_check_state(DEFAULT_UPDATE_CHECK_PATH)
        for warning in warnings:
            logger.warning(warning)
        state.skipped_version = available_update.version
        try:
            save_update_check_state(DEFAULT_UPDATE_CHECK_PATH, state)
        except OSError as exc:
            logger.warning("Could not save update-check state to %s: %s", DEFAULT_UPDATE_CHECK_PATH, exc)

    def approve_update() -> None:
        if available_update is None:
            return
        webbrowser.open(available_update.html_url)
        logger.info("Opened release page for v%s", available_update.version)
        _mark_update_skipped()
        close_update_notice()
        player.show_text("Opened the release page in your browser", duration_ms=3000)

    def decline_update() -> None:
        logger.info("Update notice dismissed")
        _mark_update_skipped()
        close_update_notice()

    def open_update_notice() -> None:
        nonlocal update_notice_visible, plex_reopen_pending
        if available_update is None:
            return
        # Mutual exclusivity with every other overlay, matching every
        # toggle_X -- this is a background-thread-triggered "open" rather
        # than a keypress-triggered toggle, but reuses the identical
        # close-others-first convention rather than inventing a new
        # "wait until nothing's open" deferral. Each close_X() here is
        # only ever actually reached if its own X_visible flag is set,
        # so this is safe to call unconditionally regardless of session
        # type (channel, Plex, or neither).
        if guide_visible:
            close_guide()
        if recordings_visible:
            close_recordings_browser()
        if schedule_browser_visible:
            close_schedule_browser()
        if vod_visible:
            close_vod_browser()
        if help_visible:
            close_help_overlay()
        if about_visible:
            close_about_overlay()
        if plex_visible:
            close_plex_browser()
            plex_reopen_pending = True
        if chromecast_visible:
            close_chromecast_picker()

        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_update_available_overlay(available_update.version, __version__, osd_size[0], osd_size[1])
        x = (osd_size[0] - image.width) // 2
        y = (osd_size[1] - image.height) // 2
        player.show_overlay(image, x=x, y=y, overlay_id=_UPDATE_OVERLAY_ID)
        player.on_key_press("y", approve_update)
        player.on_key_press("n", decline_update)
        player.on_key_press("ESC", decline_update)
        update_notice_visible = True
        logger.info("Update notice shown: v%s available", available_update.version)

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
                if playing_vod_item is not None:
                    _save_current_vod_position()
                    # Same cadence Plex's own clients report on -- rides
                    # this already-running loop rather than a separate
                    # timer. _report_plex_state no-ops for anything that
                    # isn't a currently-playing Plex item.
                    _report_plex_state("paused" if player.is_paused else "playing", background=False)
            except Exception:
                logger.exception("Error while autosaving playback position")
            if playback_autosave_stop_event.wait(_PLAYBACK_POSITION_AUTOSAVE_SECONDS):
                return

    def handle_playback_error() -> None:
        # A stream that fails to open or drops mid-playback (dead server,
        # rejected request, network drop, etc.) leaves mpv with no video
        # track -- without force_window (see Player.__init__), that would
        # drop the window entirely and, with it, all further keyboard
        # input. A recording is a local file (a read error there means
        # something like corruption or deletion, not a reconnectable
        # network problem) so it's never retried; a live channel or VOD
        # stream is retried with backoff (see _RECONNECT_DELAYS_SECONDS)
        # until _RECONNECT_MAX_ATTEMPTS is reached, at which point this
        # falls back to the original behavior: surface the failure and, if
        # there's a guide to fall back on, reopen it so the app stays
        # usable instead of silently stranding the user on a blank,
        # unresponsive window.
        nonlocal reconnect_attempt, reconnect_timer
        cancel_reconnect_stability_timer()  # a flap right after "stable" shouldn't already have reset the counter

        if playing_vod_item is not None:
            label, target_url = playing_vod_item.title, playing_vod_item.url
        elif channel is not None:
            label, target_url = channel.name, channel.url
        else:
            label, target_url = (title or url), url

        if playing_recording is not None or reconnect_attempt >= _RECONNECT_MAX_ATTEMPTS:
            player.show_text(f"Failed to play {label}", duration_ms=4000)
            logger.error("Failed to play %s (%s)", label, target_url)
            reconnect_attempt = 0
            if channel is not None and display is not None and not guide_visible:
                toggle_guide()
            return

        delay = _RECONNECT_DELAYS_SECONDS[min(reconnect_attempt, len(_RECONNECT_DELAYS_SECONDS) - 1)]
        reconnect_attempt += 1
        attempt = reconnect_attempt
        # A VOD item is a real, seekable file with a meaningful position to
        # resume from; a live channel has no such thing once its stream
        # restarts, so it just rejoins at the live edge like any other
        # channel switch. Reading the position now (rather than the last
        # periodic autosave) captures wherever playback actually was at the
        # moment it dropped.
        resume_at = None
        if playing_vod_item is not None:
            position_and_duration = player.playback_position()
            resume_at = position_and_duration[0] if position_and_duration is not None else playback_positions.get(target_url)

        player.show_text(
            f"Connection lost. Reconnecting to {label} (attempt {attempt}/{_RECONNECT_MAX_ATTEMPTS})...",
            duration_ms=int(delay * 1000) + 1000,
        )
        logger.warning("Playback error for %s; reconnecting in %.0fs (attempt %d/%d)", label, delay, attempt, _RECONNECT_MAX_ATTEMPTS)

        def _attempt_reconnect() -> None:
            player.play(target_url, title=label, start=resume_at)
            if recording_path is not None:
                player.start_recording(str(recording_path))

        reconnect_timer = threading.Timer(delay, _attempt_reconnect)
        reconnect_timer.daemon = True
        reconnect_timer.start()

    def handle_playback_started() -> None:
        # Fires on every successful file load, not just ones following a
        # reconnect. Logged unconditionally (not just the reconnect path)
        # so a session log can distinguish "never connected" (nothing
        # between "Starting playback"/"Playback error" and the next log
        # line) from "connected fine, then dropped later" -- otherwise the
        # two look identical from the log alone.
        nonlocal reconnect_stability_timer
        logger.info("Playback started")
        if reconnect_attempt == 0:
            return
        cancel_reconnect_stability_timer()

        def _mark_stable() -> None:
            nonlocal reconnect_attempt
            reconnect_attempt = 0
            logger.info("Playback stable after reconnect; retry backoff reset")

        reconnect_stability_timer = threading.Timer(_RECONNECT_STABLE_SECONDS, _mark_stable)
        reconnect_stability_timer.daemon = True
        reconnect_stability_timer.start()

    player.on_playback_error(handle_playback_error)
    player.on_playback_started(handle_playback_started)

    logger.info("Starting playback: %s (%s)", title or url, url)
    try:
        if plex_creds is None:
            # playing_vod_item is only non-None here for a local-file
            # launch (see initial_vod_item) -- every other caller leaves
            # it unset at this point, so resume_at stays None and this is
            # a no-op for them, same as before this was added.
            resume_at = playback_positions.get(playing_vod_item.url) if playing_vod_item is not None else None
            player.play(url, title=title, start=resume_at)
            if channel is not None:
                _start_history_entry("channel", channel.name, channel.url)
            elif playing_vod_item is not None:
                _start_history_entry("vod", playing_vod_item.title, playing_vod_item.url)
            else:
                # A bare direct-stream URL (main()'s non-M3U fallback) --
                # no EPG/guide and no VOD resume semantics, so "channel" is
                # the closer fit of the two: a single, ungoverned stream,
                # same shape as a channel with no guide data.
                _start_history_entry("channel", title or url, url)
            if resume_at:
                player.show_text(f"Resuming: {title or url}", duration_ms=3000)
                logger.info("Resuming at %.0fs: %s", resume_at, url)
        # A Plex session has nothing to play yet -- force_window (see
        # Player.__init__) keeps the window/input alive with nothing
        # loaded, exactly as it already does for a failed direct-stream
        # URL; the Plex browser (opened further below) is what puts
        # something on screen for the user to actually pick.
        player.on_key_press("z", cycle_aspect_ratio)  # available for any playback, not just EPG-backed channels
        player.on_key_press("r", toggle_recording)  # ditto
        player.on_key_press("?", toggle_help_overlay)  # ditto
        player.on_key_press("p", toggle_live_pause)  # ditto
        player.on_key_press("o", toggle_picture_in_picture)  # ditto
        player.on_key_press("t", toggle_subtitles)  # ditto
        player.on_key_press("a", toggle_about_overlay)  # ditto
        player.on_key_press("k", toggle_chromecast_picker)  # ditto -- casts whatever's currently playing
        player.on_key_press("x", toggle_history_browser)  # ditto -- browses watch history regardless of source
        # GO_BACK is the key name mpv reports for a remote's dedicated
        # back button -- rather than duplicating every single ESC binding
        # site throughout this app (there are dozens: every browser/
        # overlay/prompt's own close/cancel), synthesize a real ESC
        # keypress and let mpv's normal dispatch handle it, so GO_BACK
        # always does exactly whatever ESC currently would, with no
        # further wiring needed anywhere else.
        player.on_key_press("GO_BACK", lambda: player.synthesize_key_press("ESC"))
        # BS is the key name mpv reports for at least one real remote's
        # dedicated "DEL" button (confirmed live: it fell through to
        # mpv's own default BS binding, "set speed 1.0", before this) --
        # repurposed here as a "stop playback" button, the closest
        # equivalent this single-window, always-something-loaded app has
        # (mpv itself treats a remote's STOP key the same way, via its
        # own default STOP->quit binding). Shadowed by the guide filter/
        # Plex search/Plex year text-entry prompts' own BS "delete last
        # character" binding while one of those is open, and restored
        # by each one's own finish_* function once it closes.
        player.on_key_press("BS", player.quit_playback)
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
        # The OK/center button on IR/BLE air-mouse remotes sends ENTER --
        # this is its base, "nothing else is open" meaning for any session
        # type (channel, Plex, VOD/local-file/YouTube alike): play/pause
        # whatever's currently playing, mirroring PLAY/PAUSE/PLAYPAUSE
        # above rather than mpv's own unbound default. Every browser that
        # temporarily takes ENTER over for its own "confirm selection"
        # meaning (guide, recordings/VOD/schedule/history/Plex browsers,
        # the chromecast picker) restores this exact binding when it
        # closes -- see each one's own close_X.
        player.on_key_press("ENTER", toggle_live_pause)

        playback_autosave_thread = threading.Thread(target=_playback_position_autosave_loop, daemon=True)
        playback_autosave_thread.start()

        if update_checker is not None:
            # Whether tvdinner itself is up to date is orthogonal to
            # channel/EPG state -- unlike the EPG/online-logos loaders
            # below, this runs for every session type (channel, Plex, or
            # a bare direct stream), not just channel-backed playback.
            def _check_for_update_in_background() -> None:
                nonlocal available_update
                available_update = update_checker()
                if available_update is not None:
                    open_update_notice()

            threading.Thread(target=_check_for_update_in_background, daemon=True).start()

        if playing_vod_item is not None and channel is None and plex_creds is None:
            # A bare local-file launch (main()'s local-video-file branch)
            # -- no guide and no Plex browser to fall through from, so 'i'
            # needs its own binding here (mirrors the Plex-only session's
            # identical binding further below). MENU alongside it, same
            # reasoning as the channel/Plex sessions' own MENU binding --
            # an air-mouse remote's MENU button should show this overlay
            # here too, not silently fall through to mpv's own unused
            # on-screen-select-script default.
            player.on_key_press("i", show_vod_info_overlay)
            player.on_key_press("MENU", show_vod_info_overlay)

        if vod_metadata_loader is not None:
            # TMDB lookup for the file's guessed identity (see main()'s
            # local-video-file branch) -- runs once in the background,
            # same pattern as update_checker above, so playback never
            # waits on it.
            # playing_vod_item is a plain nonlocal rebind, atomic under the
            # GIL, so this can't race show_vod_info_overlay's or
            # _save_current_vod_position's reads of it.
            def _load_vod_metadata_in_background() -> None:
                nonlocal playing_vod_item
                enriched = vod_metadata_loader()
                if enriched is not None:
                    playing_vod_item = enriched
                    logger.info("TMDB metadata found for %s", enriched.title)
                else:
                    logger.info("No TMDB metadata found for %s", title or url)

            threading.Thread(target=_load_vod_metadata_in_background, daemon=True).start()

        def _enrich_vod_hero_art_in_background(item: VodItem) -> None:
            # Best-effort, non-blocking TMDB title/year match purely for
            # backdrop_url/logo_url (tmdb.MovieMetadata.backdrop_url/
            # logo_url) -- lets a VOD item from a source with no wide
            # backdrop/logo art of its own (Xtream, Stalker, a bare M3U
            # --vod-group entry; Plex supplies its own backdrop via its
            # 'art' field, see plex.resolve_plex_playable, but never a
            # title logo) still get overlay.render_vod_info_overlay's
            # full-bleed hero treatment (and its top-right logo) for the
            # 'i' key, once TMDB has a match. Deliberately narrower than
            # vod_metadata_loader above, which replaces the *entire*
            # VodItem with TMDB's own poster/rating/description/director
            # -- appropriate there since TMDB is the only metadata source
            # at all for a local file/YouTube video, but wrong here, where
            # the source's own poster/rating/description/director are
            # already real and shouldn't be silently overwritten by a
            # possibly-wrong TMDB match just to get its backdrop/logo.
            # Only fills in whichever of the two fields `item` doesn't
            # already have (e.g. a Plex item keeps its own real backdrop,
            # just gains a TMDB logo on top of it) -- no-op entirely once
            # both are already set, or if there's no --tmdb-api-token
            # configured, or it has no title to search on. A Plex TV
            # episode (item.series_title set -- see VodItem's own
            # docstring) searches TMDB's /search/tv by the show's name
            # instead of /search/movie by the episode's own title.
            if (item.backdrop_url and item.logo_url) or not tmdb_api_token or not item.title:
                return

            def _lookup() -> None:
                nonlocal playing_vod_item
                if item.series_title:
                    # A Plex TV episode -- item.title is the episode's
                    # own title, useless for a /search/movie lookup (see
                    # VodItem.series_title's own docstring), and Plex
                    # already supplies a real backdrop_url here, so only
                    # the logo is worth a TMDB round trip.
                    backdrop_url = item.backdrop_url
                    logo_url = item.logo_url or fetch_tv_logo_cached(item.series_title, item.year, tmdb_api_token)
                else:
                    metadata = fetch_movie_metadata_cached(item.title, item.year, tmdb_api_token)
                    if metadata is None:
                        return
                    backdrop_url = item.backdrop_url or metadata.backdrop_url
                    logo_url = item.logo_url or metadata.logo_url
                if backdrop_url == item.backdrop_url and logo_url == item.logo_url:
                    return  # TMDB had nothing new to offer either field
                # Discard a stale result if the user has since moved on to
                # a different item (or nothing at all) while this lookup
                # was in flight -- `is` identity, not equality, since two
                # distinct VodItems can legitimately share a title (e.g. a
                # boxset's disc 1/disc 2).
                if playing_vod_item is item:
                    playing_vod_item = replace(item, backdrop_url=backdrop_url, logo_url=logo_url)
                    logger.info("TMDB hero art found for %s", item.title)
                    # Same "redraw immediately once the fetch completes"
                    # reasoning as show_epg_overlay's own
                    # _redraw_once_backdrop_ready -- without this, a
                    # backdrop/logo landing after the popup's very first,
                    # automatic showing (right when playback starts, before
                    # this background lookup could possibly have finished)
                    # would never be seen at all unless the user happened
                    # to press 'i' again later. hide_timer is only ever
                    # non-None while this exact popup is currently shown
                    # (see show_vod_info_overlay), so this is a no-op if
                    # it's since been dismissed.
                    if hide_timer is not None:
                        show_vod_info_overlay()

            threading.Thread(target=_lookup, daemon=True).start()

        if channel is not None and display is not None:
            # A real playlist with no discoverable EPG source (e.g. no
            # x-tvg-url/tvg-url at all) still gets the guide/OSD keybindings
            # -- they just report "no data" instead of silently doing
            # nothing, which otherwise looked indistinguishable from the
            # keys not being bound at all.
            epg = epg or Epg()

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
                    loaded = epg_loader(
                        lambda message: player.show_text(message, duration_ms=_EPG_PROGRESS_OSD_DURATION_MS)
                    )
                    if loaded is not None:
                        epg = loaded
                        print(f"EPG data loaded ({len(loaded.channels)} channels).", file=sys.stderr)
                        logger.info("EPG data loaded (%d channels)", len(loaded.channels))
                        player.show_text(f"EPG data loaded ({len(loaded.channels)} channels)", duration_ms=3000)
                    else:
                        print("EPG data not available.", file=sys.stderr)
                        logger.warning("EPG data not available")
                        player.show_text("EPG data not available", duration_ms=3000)

                print("Loading EPG data...", file=sys.stderr)
                player.show_text("Loading EPG data...", duration_ms=_EPG_PROGRESS_OSD_DURATION_MS)
                threading.Thread(target=_load_epg_in_background, daemon=True).start()

            if online_logos_loader is not None:
                # Same reasoning as the EPG load above: don't block playback
                # on iptv-org's ~17MB combined channel/logo database (which
                # itself is on-disk cached, so this is usually much faster
                # than that -- see channel_logos.load_online_logo_index),
                # and the same atomic-name-rebind safety applies.
                def _load_online_logos_in_background() -> None:
                    nonlocal online_logos
                    online_logos = online_logos_loader()
                    logger.info("Online logo index ready (%d channels)", len(online_logos.by_id))

                threading.Thread(target=_load_online_logos_in_background, daemon=True).start()

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

                if playing_vod_item is not None:
                    # Playing a VOD movie/episode (from the 'm' browser) --
                    # same reasoning as the recording case above, there's no
                    # live EPG to show for this either.
                    show_vod_info_overlay()
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

                canvas_width, canvas_height = _resolve_canvas_size(player)
                image = render_epg_overlay(
                    channel,
                    current,
                    upcoming,
                    display,
                    now,
                    logo=resolve_channel_logo(channel, epg, online_logos),
                    canvas_width=canvas_width,
                    canvas_height=canvas_height,
                    badges=badges,
                    favorites=favorites,
                )
                # A resolved TMDB backdrop switches this to the full-bleed
                # hero treatment (see render_epg_overlay's dispatch), sized
                # to exactly fill the screen -- placed at the origin rather
                # than the ordinary banner's flush-left-under-the-top-
                # safe-area position (the banner spans the full video width
                # but not its height, so it needs that top gap; the hero
                # doesn't).
                y = 0 if image.height == canvas_height else _OVERLAY_TOP_MARGIN
                player.show_overlay(image, x=0, y=y)

                hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
                hide_timer.daemon = True
                hide_timer.start()

                if tmdb_api_token is not None and current is not None and is_movie_category(current.category, channel.group_title):
                    # Same non-blocking pattern as render_and_show_guide's own
                    # prefetch -- this draw above already used whatever was
                    # cached; kicking this off just means the banner picks up
                    # the rating on its next show (channel switch, or 'i').
                    prefetch_ratings({(current.title, current.year)}, tmdb_api_token)
                    # Skipped when the feed's own <credits><director> already
                    # gave render_epg_overlay one (see show_selected_details's
                    # identical guard for the guide's details popup).
                    if not current.director:
                        prefetch_director({(current.title, current.year)}, tmdb_api_token)
                    # For the full-bleed hero treatment above (and its
                    # top-right title logo), once either lands -- see
                    # render_epg_overlay's own dispatch. Unlike rating/
                    # director, redraw immediately once a fetch completes
                    # rather than waiting for the next unrelated redraw
                    # (resize/mouse-move/'i'): a backdrop switches the
                    # *entire* overlay layout from banner to hero, and the
                    # very first automatic show (right after a channel
                    # switch) can never win that race on its own, since
                    # the prefetch it needs is the one being kicked off
                    # right here. The logo prefetch shares this same
                    # callback -- a harmless extra redraw if both land
                    # close together. Guarded against a stale fetch from a
                    # channel/programme the user has since left firing late
                    # and popping the overlay back up.
                    backdrop_key = (current.title, current.year)
                    expected_channel_url = channel.url

                    def _redraw_once_backdrop_ready(key: tuple[str, str | None] = backdrop_key) -> None:
                        if hide_timer is None or channel.url != expected_channel_url:
                            return  # overlay dismissed, or a different channel is on screen now
                        latest, _ = current_and_next_programmes(channel, epg, display, datetime.now(timezone.utc))
                        if latest is None or (latest.title, latest.year) != key:
                            return  # the programme has since changed (e.g. it ended)
                        show_epg_overlay()

                    prefetch_backdrop({backdrop_key}, tmdb_api_token, on_fetched=_redraw_once_backdrop_ready)
                    prefetch_logo({backdrop_key}, tmdb_api_token, on_fetched=_redraw_once_backdrop_ready)

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
                if guide_filter:
                    needle = guide_filter.lower()
                    base = [
                        c
                        for c in base
                        if needle in c.name.lower() or any(needle in g.lower() for g in c.groups)
                    ]
                return hd_first(base)

            def resolved_guide_window_start() -> datetime:
                if guide_window_start is not None:
                    return guide_window_start
                now = datetime.now(timezone.utc)
                return now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 30)

            def _render_guide_from_logo_refresh_timer() -> None:
                # The timer's own target rather than render_and_show_guide
                # directly, so guide_visible/details_visible are rechecked
                # right before actually rendering -- closing the guide (or
                # opening programme details over it) in the debounce
                # window between _on_guide_logo_resolved scheduling this
                # and it actually firing shouldn't pop the guide back up.
                nonlocal guide_logo_refresh_timer
                guide_logo_refresh_timer = None
                if guide_visible and not details_visible:
                    render_and_show_guide()

            def _on_guide_logo_resolved() -> None:
                # Runs on the resolving background thread (see
                # overlay.prefetch_channel_logos), potentially once per
                # channel on the page -- debounced into a single
                # re-render a moment later rather than one per completed
                # fetch. Without this, a guide left untouched right after
                # opening it stayed on placeholder avatars indefinitely,
                # even once every logo had long since finished loading --
                # only some *later*, unrelated re-render (paging, a
                # channel switch, ...) ever picked them up.
                nonlocal guide_logo_refresh_timer
                if not guide_visible or details_visible:
                    return
                cancel_guide_logo_refresh_timer()
                guide_logo_refresh_timer = threading.Timer(
                    _GUIDE_LOGO_REFRESH_DEBOUNCE_SECONDS, _render_guide_from_logo_refresh_timer
                )
                guide_logo_refresh_timer.daemon = True
                guide_logo_refresh_timer.start()

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

                # Never blocking: only spawns background fetches for logos
                # not already cached/in-flight (see
                # overlay.prefetch_channel_logos) -- the image just rendered
                # above already used cached_channel_logo's cache-only read
                # (falling back to a placeholder avatar for anything not yet
                # resolved). _on_guide_logo_resolved debounces a follow-up
                # re-render once those fetches land, rather than leaving a
                # freshly-opened, untouched guide stuck on placeholders
                # until some unrelated later render happens to pick them up.
                prefetch_channel_logos(
                    visible_guide_channels(guide_channel_list(), epg, selected_channel_url or channel.url, max_rows=_GUIDE_MAX_ROWS),
                    epg,
                    online_logos,
                    on_resolved=_on_guide_logo_resolved,
                )

                if tmdb_api_token is not None:
                    # Never blocking: this only spawns background fetches for
                    # movies not already cached/in-flight (see
                    # tmdb.prefetch_ratings) -- the image just rendered above
                    # shows whatever was already cached, and a badge for a
                    # newly-fetched rating appears on the next render.
                    movies = visible_guide_movies(
                        guide_channel_list(),
                        epg,
                        display,
                        datetime.now(timezone.utc),
                        window_start=guide_window_start,
                        max_rows=_GUIDE_MAX_ROWS,
                        current_channel_url=channel.url,
                        selected_channel_url=selected_channel_url,
                    )
                    prefetch_ratings(movies, tmdb_api_token)

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
                # (it covers every letter, including g/i/z/h/r/w/u/m/b/p/o/t/a/k/x/j's normal meanings).
                player.on_key_press("g", toggle_guide)
                player.on_key_press("i", show_epg_overlay)
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("h", toggle_favorite)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("w", toggle_recordings_browser)
                player.on_key_press("u", toggle_schedule_browser)
                player.on_key_press("m", toggle_vod_browser)
                player.on_key_press("b", switch_to_last_channel)
                player.on_key_press("p", toggle_live_pause)
                player.on_key_press("o", toggle_picture_in_picture)
                player.on_key_press("t", toggle_subtitles)
                player.on_key_press("a", toggle_about_overlay)
                player.on_key_press("k", toggle_chromecast_picker)
                player.on_key_press("x", toggle_history_browser)
                player.on_key_press("BS", player.quit_playback)
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
                    logo=resolve_channel_logo(selected_channel, epg, online_logos),
                )
                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_DETAILS_OVERLAY_ID)
                details_visible = True
                details_channel = selected_channel
                details_programme = programme

                if tmdb_api_token is not None and is_movie_category(programme.category, selected_channel.group_title):
                    # Same non-blocking pattern as render_and_show_guide's own
                    # prefetch -- this draw above already used whatever was
                    # cached; kicking this off just means a repeat view (or
                    # the guide) picks up the rating soon after.
                    prefetch_ratings({(programme.title, programme.year)}, tmdb_api_token)
                    # Director isn't bulk-prefetched for every visible grid
                    # movie the way rating is (see tmdb._director_cache's own
                    # comment) -- only kicked off here, for the one programme
                    # whose details were actually opened. Skipped entirely
                    # when the feed's own <credits><director> already gave
                    # render_programme_details one (see overlay.py) -- no
                    # point spending a TMDB request on a field we're not
                    # even going to show.
                    if not programme.director:
                        prefetch_director({(programme.title, programme.year)}, tmdb_api_token)
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
                cancel_guide_logo_refresh_timer()
                player.clear_overlay(overlay_id=_GUIDE_OVERLAY_ID)
                unbind_guide_navigation_keys()
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
                guide_visible = False
                logger.info("Guide closed")

            def switch_to_channel(new_channel: Channel) -> None:
                nonlocal channel, playing_recording, playing_vod_item, last_channel
                _save_current_recording_position()
                _save_current_vod_position()
                _end_current_history_entry()
                _reset_reconnect_state()
                last_channel = channel
                channel = new_channel
                playing_recording = None  # back to live TV -- 'i' should show its EPG info again, not a stale recording
                playing_vod_item = None
                player.play(channel.url, title=channel.name)
                _start_history_entry("channel", channel.name, channel.url)
                show_epg_overlay()
                logger.info("Switched to channel '%s' (%s)", channel.name, channel.url)

            def switch_to_last_channel() -> None:
                # 'b' (back): jumps to whatever channel was playing right
                # before this one, if any -- repeated presses toggle back
                # and forth, since switch_to_channel always records the
                # channel it's leaving, including the one this lands on.
                if last_channel is None:
                    return
                switch_to_channel(last_channel)

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
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
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
                nonlocal playing_recording, playing_vod_item
                if not recordings_visible or recordings_selected_path is None:
                    return
                selected = next((r for r in recordings_list if r.path == recordings_selected_path), None)
                if selected is None:
                    return
                close_recordings_browser()
                _save_current_recording_position()  # in case we were already watching a different one
                _save_current_vod_position()
                _end_current_history_entry()
                _reset_reconnect_state()
                playing_vod_item = None
                playing_recording = selected
                resume_at = playback_positions.get(str(selected.path))
                player.play(str(selected.path), title=selected.label, start=resume_at)
                _start_history_entry("recording", selected.label, str(selected.path))
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
                if vod_visible:
                    close_vod_browser()
                if about_visible:
                    close_about_overlay()
                if history_browser_visible:
                    close_history_browser()
                open_recordings_browser()

            def close_vod_browser() -> None:
                nonlocal vod_visible, vod_selected_index
                if not vod_visible:
                    return
                player.clear_overlay(overlay_id=_VOD_OVERLAY_ID)
                player.unbind_key("UP")
                player.unbind_key("DOWN")
                player.unbind_key("PGUP")
                player.unbind_key("PGDWN")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
                vod_visible = False
                vod_selected_index = 0
                logger.info("VOD browser closed")

            def render_and_show_vod() -> bool:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_vod_browser(
                    vod_list,
                    vod_selected_index,
                    osd_size[0],
                    osd_size[1],
                    max_rows=_VOD_MAX_ROWS,
                )
                if image is None:
                    return False
                x = (osd_size[0] - image.width) // 2
                y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
                player.show_overlay(image, x=x, y=y, overlay_id=_VOD_OVERLAY_ID)
                return True

            def move_vod_selection(step: int) -> None:
                nonlocal vod_selected_index
                if not vod_visible or not vod_list:
                    return
                vod_selected_index = max(0, min(len(vod_list) - 1, vod_selected_index + step))
                render_and_show_vod()

            def play_selected_vod_item() -> None:
                nonlocal playing_recording, playing_vod_item
                if not vod_visible or not vod_list:
                    return
                selected = vod_list[vod_selected_index]
                close_vod_browser()
                _save_current_recording_position()  # in case we were already watching a recording
                _save_current_vod_position()  # in case we were already watching a different VOD item
                _end_current_history_entry()
                _reset_reconnect_state()
                playing_recording = None
                playing_vod_item = selected
                _enrich_vod_hero_art_in_background(selected)
                resume_at = playback_positions.get(selected.url)
                player.play(selected.url, title=selected.title, start=resume_at)
                _start_history_entry("vod", selected.title, selected.url)
                if resume_at:
                    player.show_text(f"Resuming: {selected.title}", duration_ms=3000)
                    logger.info("Resuming VOD item at %.0fs: %s", resume_at, selected.url)
                else:
                    player.show_text(f"Playing: {selected.title}", duration_ms=3000)
                    logger.info("Playing VOD item: %s", selected.url)

            def open_vod_browser() -> None:
                nonlocal vod_visible, vod_selected_index
                if not vod_list:
                    player.show_text("No VOD movies found", duration_ms=3000)
                    return

                vod_selected_index = 0
                if render_and_show_vod():
                    vod_visible = True
                    player.on_key_press("UP", lambda: move_vod_selection(-1))
                    player.on_key_press("DOWN", lambda: move_vod_selection(1))
                    player.on_key_press("PGUP", lambda: move_vod_selection(-_VOD_MAX_ROWS))
                    player.on_key_press("PGDWN", lambda: move_vod_selection(_VOD_MAX_ROWS))
                    player.on_key_press("ENTER", play_selected_vod_item)
                    player.on_key_press("KP_ENTER", play_selected_vod_item)
                    player.on_key_press("ESC", close_vod_browser)
                    logger.info("VOD browser opened (%d items)", len(vod_list))

            def toggle_vod_browser() -> None:
                if vod_visible:
                    close_vod_browser()
                    return
                if guide_visible:
                    close_guide()
                if recordings_visible:
                    close_recordings_browser()
                if schedule_browser_visible:
                    close_schedule_browser()
                if help_visible:
                    close_help_overlay()
                if about_visible:
                    close_about_overlay()
                if history_browser_visible:
                    close_history_browser()
                open_vod_browser()

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
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
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
                if vod_visible:
                    close_vod_browser()
                if about_visible:
                    close_about_overlay()
                if history_browser_visible:
                    close_history_browser()
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
                if vod_visible:
                    close_vod_browser()
                if about_visible:
                    close_about_overlay()
                if history_browser_visible:
                    close_history_browser()

                # Showing the guide replaces the small info banner rather than
                # layering on top of it, and always opens on the current time
                # with any previous filter cleared.
                cancel_hide_timer()
                player.clear_overlay()
                # Opening the guide for the first time in a session (or after
                # scrolling to reveal channels never shown before) can still
                # take a moment even with logo fetches backgrounded (see
                # prefetch_channel_logos) -- large EPG feeds cost real time to
                # filter/lay out. This OSD message is superseded by the guide
                # overlay itself as soon as render_and_show_guide finishes.
                player.show_text("Loading guide...", duration_ms=2000)
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
            # ENTER used to mirror this (the OK/center button on IR/BLE
            # air-mouse remotes typically sends ENTER), but that's now
            # play/pause instead -- see the universal ENTER binding
            # earlier in this function -- so MENU's short press (below)
            # is the remote's way in to this overlay instead.
            player.on_key_press("i", show_epg_overlay)
            player.on_resize(on_resize)  # keep the overlay correctly sized as the window is resized
            player.on_key_press("MOUSE_MOVE", on_mouse_move)  # trackpad/mouse activity reveals it too
            player.on_key_press("g", toggle_guide)  # press 'g' to toggle the full program guide
            player.on_key_press("b", switch_to_last_channel)  # 'b' (back) jumps to the previously watched channel
            player.on_key_press("h", toggle_favorite)  # 'h' (heart) favorites the playing/selected channel
            player.on_key_press("w", toggle_recordings_browser)  # 'w' (watch) browses past recordings
            player.on_key_press("u", toggle_schedule_browser)  # 'u' (upcoming) browses scheduled recordings
            player.on_key_press("m", toggle_vod_browser)  # 'm' (movies) browses VOD movies
            # The MENU button on IR/BLE air-mouse remotes sends MENU (mpv's
            # own default binds it to the on-screen 'select' script's menu --
            # harmless to override, since this app doesn't use that script).
            # Unlike ENTER, MENU isn't a guide-only key anywhere else, so no
            # shadowing/restoring is needed. Confirmed live (see CLAUDE.md)
            # that a real remote's OK/MENU buttons send a genuine, reliably
            # distinguishable key-down/key-up pair for a tap vs. a hold --
            # short press shows the same EPG overlay ENTER used to, long
            # press (>=0.5s) opens the full guide (what a plain MENU press
            # did before).
            player.on_key_press_or_hold("MENU", on_press=show_epg_overlay, on_hold=toggle_guide)

        if plex_creds is not None:
            # Sibling to the "if channel is not None and display is not
            # None:" block above, not nested inside it -- a Plex session
            # has neither a channel nor an EPG display, so none of that
            # block's guide/VOD/recordings/schedule machinery or
            # keybindings are ever defined here. Auto-opened once,
            # immediately below, since a Plex-only launch has nothing
            # else on screen for the user to look at.

            def close_plex_browser() -> None:
                # Deliberately leaves plex_nav_stack untouched -- this only
                # hides the overlay (called both for an explicit close and
                # for select_plex_node's own "close before playing" step,
                # plus every other browser's mutual-exclusivity check), not
                # a "leave Plex" action. Clearing it here used to mean 'l'
                # always reopened at the library root, even right after
                # playing an episode -- open_plex_browser's own `if not
                # plex_nav_stack:` guard is what seeds the root frame the
                # very first time, and is the only place that should.
                nonlocal plex_visible
                if not plex_visible:
                    return
                cancel_plex_image_refresh_timer()
                player.clear_overlay(overlay_id=_PLEX_OVERLAY_ID)
                for key in ("UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC", "/", "y"):
                    player.unbind_key(key)
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
                plex_visible = False
                logger.info("Plex browser closed")

            def _render_plex_from_image_refresh_timer() -> None:
                # The timer's own target rather than render_and_show_plex
                # directly, so plex_visible is rechecked right before
                # actually rendering -- same reasoning as
                # _render_history_from_image_refresh_timer above.
                nonlocal plex_image_refresh_timer
                plex_image_refresh_timer = None
                if plex_visible:
                    render_and_show_plex()

            def _on_plex_image_resolved() -> None:
                # Runs on the resolving background thread (see
                # overlay.prefetch_images), potentially once per row on the
                # page -- debounced into a single re-render, same reasoning
                # as _on_history_image_resolved above.
                nonlocal plex_image_refresh_timer
                if not plex_visible:
                    return
                cancel_plex_image_refresh_timer()
                plex_image_refresh_timer = threading.Timer(
                    _GUIDE_LOGO_REFRESH_DEBOUNCE_SECONDS, _render_plex_from_image_refresh_timer
                )
                plex_image_refresh_timer.daemon = True
                plex_image_refresh_timer.start()

            def plex_frame_nodes(frame: _PlexNavFrame) -> list[PlexNode]:
                # frame.nodes is always the full, unfiltered listing --
                # filtering happens here, live, at render/selection time
                # (mirrors guide_channel_list's favorites_only handling),
                # so toggling the filter never has to mutate or reconcile
                # two copies of a frame's node list.
                if not plex_favorites_only:
                    return frame.nodes
                return [n for n in frame.nodes if n.kind in _PLEX_FAVORITABLE_KINDS and n.rating_key in favorites]

            def render_and_show_plex() -> bool:
                nonlocal plex_favorites_only
                frame = plex_nav_stack[-1]
                nodes = plex_frame_nodes(frame)
                if not nodes and plex_favorites_only and frame.nodes:
                    # The filter emptied out an otherwise non-empty frame --
                    # e.g. drilling into a show's seasons (never
                    # favoritable) while favorites-only was on, or
                    # unfavoriting the one item that made this the
                    # filtered view in the first place. Every key handler
                    # below (move_plex_selection, select_plex_node,
                    # toggle_plex_favorite) guards on this same filtered
                    # list being non-empty, so leaving it empty here is a
                    # total dead end -- confirmed live, arrow keys and 'h'
                    # both silently stopped doing anything at all. Falling
                    # back to the unfiltered list instead means the
                    # browser is never stuck on a frozen, unresponsive
                    # frame just because of what the filter happened to
                    # match at this particular level.
                    plex_favorites_only = False
                    nodes = frame.nodes
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                if plex_grid_view:
                    image = render_plex_grid_browser(
                        frame.breadcrumb,
                        nodes,
                        frame.selected_index,
                        osd_size[0],
                        osd_size[1],
                        columns=_PLEX_GRID_COLUMNS,
                        max_rows=_PLEX_GRID_ROWS,
                        favorites=favorites,
                    )
                else:
                    image = render_plex_browser(
                        frame.breadcrumb,
                        nodes,
                        frame.selected_index,
                        osd_size[0],
                        osd_size[1],
                        max_rows=_PLEX_MAX_ROWS,
                        favorites=favorites,
                    )
                if image is None:
                    return False
                # Unlike every other browser overlay here, this one is now
                # always the full osd_size -- a full-bleed poster backdrop
                # behind the panel (see overlay._plex_full_backdrop), not
                # just the panel itself -- so there's no panel size to
                # bottom-anchor against here; render_plex_browser/
                # render_plex_grid_browser do that internally instead.
                player.show_overlay(image, x=0, y=0, overlay_id=_PLEX_OVERLAY_ID)

                # Never blocking: only spawns background fetches for
                # thumbnails not already cached/in-flight -- the image just
                # rendered above already used cached_image's cache-only
                # read (falling back to a placeholder for anything not yet
                # resolved). Only the currently visible page, same as
                # render_and_show_history's own thumbnail prefetch.
                if plex_grid_view:
                    visible = visible_plex_grid_nodes(nodes, frame.selected_index, columns=_PLEX_GRID_COLUMNS, max_rows=_PLEX_GRID_ROWS)
                else:
                    visible = visible_plex_nodes(nodes, frame.selected_index, max_rows=_PLEX_MAX_ROWS)
                prefetch_images((node.thumb_url for node in visible), on_resolved=_on_plex_image_resolved)
                return True

            def move_plex_selection(step: int) -> None:
                if not plex_visible or not plex_nav_stack:
                    return
                frame = plex_nav_stack[-1]
                nodes = plex_frame_nodes(frame)
                if not nodes:
                    return
                frame.selected_index = max(0, min(len(nodes) - 1, frame.selected_index + step))
                render_and_show_plex()

            # UP/DOWN/PGUP/PGDWN/LEFT/RIGHT are bound to these instead of
            # move_plex_selection directly, so a single set of bindings
            # (set once, at each of the three sites every other Plex-only
            # key needs) can serve both views -- each wrapper just checks
            # plex_grid_view at call time, the same pattern
            # toggle_live_pause already uses for checking player.is_paused,
            # rather than re-binding keys every time the view toggles.
            def plex_move_up() -> None:
                move_plex_selection(-_PLEX_GRID_COLUMNS if plex_grid_view else -1)

            def plex_move_down() -> None:
                move_plex_selection(_PLEX_GRID_COLUMNS if plex_grid_view else 1)

            def plex_move_page_up() -> None:
                move_plex_selection(-_PLEX_GRID_COLUMNS * _PLEX_GRID_ROWS if plex_grid_view else -_PLEX_MAX_ROWS)

            def plex_move_page_down() -> None:
                move_plex_selection(_PLEX_GRID_COLUMNS * _PLEX_GRID_ROWS if plex_grid_view else _PLEX_MAX_ROWS)

            def plex_move_left() -> None:
                # A real grid's LEFT/RIGHT move across columns -- list
                # view has no columns, so LEFT keeps its existing meaning
                # there instead (mirrors ESC, going back a level -- see
                # plex_back). This is the one place grid view and list
                # view genuinely disagree on what a key does; RIGHT below
                # is simpler since list view never bound it to anything.
                if plex_grid_view:
                    move_plex_selection(-1)
                else:
                    plex_back()

            def plex_move_right() -> None:
                # Unbound in list view (previously fell through to mpv's
                # own default RIGHT binding -- seeking whatever's playing
                # underneath the browser, if anything is -- harmless but
                # not useful while browsing, so swallowing it here is a
                # minor incidental improvement, not just a grid-view need).
                if plex_grid_view:
                    move_plex_selection(1)

            def toggle_plex_grid_view() -> None:
                nonlocal plex_grid_view
                if not plex_visible or not plex_nav_stack:
                    return
                plex_grid_view = not plex_grid_view
                # selected_index is a plain index into the frame's node
                # list either way (see move_plex_selection) -- list view
                # windows it one row at a time, grid view by whole rows of
                # `columns` items, but both windowing functions accept the
                # same raw index and scroll it into view themselves, so
                # there's no need to reset it here to land on a valid
                # position; leaving it alone keeps the same item focused
                # across the toggle instead of always jumping back to the
                # first item.
                render_and_show_plex()
                player.show_text("Grid view" if plex_grid_view else "List view", duration_ms=1500)
                logger.info("Plex grid view: %s", plex_grid_view)

            def toggle_plex_favorite() -> None:
                # Movie/show level only, never a season or episode -- a
                # show is favorited as a whole (see _PLEX_FAVORITABLE_KINDS),
                # so this acts on whatever's selected in the browser
                # regardless of which level of the nav stack that is.
                if not plex_visible or not plex_nav_stack:
                    return
                frame = plex_nav_stack[-1]
                nodes = plex_frame_nodes(frame)
                if not nodes:
                    return
                node = nodes[frame.selected_index]
                if node.kind not in _PLEX_FAVORITABLE_KINDS:
                    player.show_text("Only movies and shows can be favorited", duration_ms=2000)
                    return

                if node.rating_key in favorites:
                    favorites.discard(node.rating_key)
                    action = "Removed from favorites"
                else:
                    favorites.add(node.rating_key)
                    action = "Added to favorites"

                if favorites_path is not None and favorites_feed is not None:
                    try:
                        save_favorites(favorites_path, favorites_feed, favorites)
                    except OSError as exc:
                        print(f"Warning: could not save favorites to {favorites_path}: {exc}", file=sys.stderr)
                        logger.warning("Could not save favorites to %s: %s", favorites_path, exc)

                player.show_text(f"{action}: {node.title}", duration_ms=1500)
                logger.info("%s: '%s'", action, node.title)
                # Unfavoriting the selected item while favorites-only is
                # active can shrink (or empty) the filtered list out from
                # under frame.selected_index -- clamp it back into bounds
                # for whatever the filtered view looks like now, same as
                # move_plex_selection already does on every move.
                remaining = plex_frame_nodes(frame)
                frame.selected_index = max(0, min(len(remaining) - 1, frame.selected_index)) if remaining else 0
                render_and_show_plex()

            def toggle_plex_favorites_only() -> None:
                nonlocal plex_favorites_only
                if not plex_visible or not plex_nav_stack:
                    return
                plex_favorites_only = not plex_favorites_only
                turning_on = plex_favorites_only
                plex_nav_stack[-1].selected_index = 0
                rendered = render_and_show_plex()
                if turning_on and not plex_favorites_only:
                    # render_and_show_plex's own fallback just silently
                    # reverted the flag we only just set -- there was
                    # nothing at this level to filter to, whether because
                    # nothing here is favorited yet or nothing here is
                    # even favoritable at all (a library root, a show's
                    # seasons, an episode listing, ...). Without this
                    # check, the plain "All items"/"Favorites only" below
                    # would report the *reverted* state as if turning
                    # favorites-only off had been the user's own request,
                    # which it wasn't -- confirmed live: pressing 'v' at
                    # the library root always said "All items" no matter
                    # what was actually favorited elsewhere.
                    player.show_text("No favorited movies or shows", duration_ms=3000)
                elif rendered:
                    player.show_text("Favorites only" if plex_favorites_only else "All items", duration_ms=1500)
                elif plex_favorites_only:
                    # A genuinely empty frame (no nodes at all) rather than
                    # a filtering artifact -- render_and_show_plex's own
                    # fallback only ever applies when frame.nodes is
                    # non-empty, so this is the rare case it doesn't cover.
                    player.show_text("No favorited movies or shows", duration_ms=3000)
                logger.info("Plex favorites-only view: %s", plex_favorites_only)

            def _play_plex_node(node: PlexNode, force_from_start: bool = False) -> None:
                # Shared by select_plex_node's normal leaf-item path and
                # the item menu's "Play from Start" action -- the only
                # difference between them is whether an existing resume
                # position is honored (force_from_start=True skips both
                # the locally-recorded one and Plex's own reported
                # progress). Not clearing playback_positions' own stored
                # entry for this URL when forced from the start: the
                # periodic autosave loop overwrites it with the new
                # progress shortly after playback begins regardless.
                nonlocal playing_recording, playing_vod_item, plex_playback_session_id
                player.show_text("Loading...", duration_ms=2000)
                item, error = resolve_plex_playable(plex_creds, node)
                if item is None:
                    player.show_text(f"Plex error: {error}", duration_ms=4000)
                    logger.error("Plex error resolving '%s': %s", node.title, error)
                    return
                close_plex_browser()
                _save_current_recording_position()
                _save_current_vod_position()
                _report_plex_state("stopped")  # for whatever Plex item (if any) was playing before this one
                _end_current_history_entry()
                _reset_reconnect_state()
                playing_recording = None
                playing_vod_item = item
                _enrich_vod_hero_art_in_background(item)
                # A fresh id per item, not reused across items -- see
                # plex_playback_session_id's own comment.
                plex_playback_session_id = str(uuid.uuid4())
                resume_at = None
                if not force_from_start:
                    # Falls back to Plex's own reported progress (see
                    # VodItem.resume_seconds) only when tvdinner has never
                    # played this item itself -- e.g. progress made watching
                    # in Plex's own apps -- never overriding a resume
                    # position already recorded locally.
                    resume_at = playback_positions.get(item.url)
                    if resume_at is None:
                        resume_at = item.resume_seconds
                player.play(item.url, title=item.title, start=resume_at)
                _start_history_entry("vod", item.title, item.url)
                # Usually a no-op here -- player.playback_position() isn't
                # readable until mpv has probed the file a moment after
                # play() returns (see its own docstring) -- but harmless
                # either way, and the periodic autosave loop reports the
                # new session within _PLAYBACK_POSITION_AUTOSAVE_SECONDS
                # regardless of whether this particular call caught it.
                _report_plex_state("playing")
                if resume_at:
                    player.show_text(f"Resuming: {item.title}", duration_ms=3000)
                    logger.info("Resuming Plex item at %.0fs: %s", resume_at, item.url)
                else:
                    player.show_text(f"Playing: {item.title}", duration_ms=3000)
                    logger.info("Playing Plex item: %s", item.url)

            def select_plex_node() -> None:
                if not plex_visible or not plex_nav_stack:
                    return
                frame = plex_nav_stack[-1]
                nodes = plex_frame_nodes(frame)
                if not nodes:
                    return
                node = nodes[frame.selected_index]

                if node.container:
                    player.show_text("Loading...", duration_ms=2000)
                    children, error = list_plex_node_children(plex_creds, node)
                    if error:
                        player.show_text(f"Plex error: {error}", duration_ms=4000)
                        logger.error("Plex error listing '%s': %s", node.title, error)
                        return
                    if not children:
                        player.show_text("Nothing found", duration_ms=2000)
                        return
                    # A season's own title is just "Season N" -- prefix the
                    # show name (this frame's breadcrumb) so it's clear
                    # which show's season is being browsed.
                    breadcrumb = f"{frame.breadcrumb} - {node.title}" if node.kind == "season" else node.title
                    plex_nav_stack.append(_PlexNavFrame(breadcrumb=breadcrumb, nodes=children))
                    render_and_show_plex()
                    return

                _play_plex_node(node)

            def _plex_item_menu_entries(node: PlexNode) -> list[str]:
                # A show has no single file of its own to play from start
                # (it's a container -- see PlexNode.container) even though
                # marking it watched/unwatched still works fine, cascading
                # to every episode server-side.
                if node.kind == "show":
                    return ["Mark as Watched", "Mark as Unwatched"]
                return ["Play from Start", "Mark as Watched", "Mark as Unwatched"]

            def render_and_show_plex_item_menu() -> None:
                if plex_item_menu_node is None:
                    return
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                entries = _plex_item_menu_entries(plex_item_menu_node)
                image = render_plex_item_menu(plex_item_menu_node.title, entries, plex_item_menu_index, osd_size[0], osd_size[1])
                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_PLEX_ITEM_MENU_OVERLAY_ID)

            def move_plex_item_menu_selection(step: int) -> None:
                nonlocal plex_item_menu_index
                if plex_item_menu_node is None:
                    return
                entries = _plex_item_menu_entries(plex_item_menu_node)
                plex_item_menu_index = max(0, min(len(entries) - 1, plex_item_menu_index + step))
                render_and_show_plex_item_menu()

            def close_plex_item_menu() -> None:
                nonlocal plex_item_menu_node
                if plex_item_menu_node is None:
                    return
                plex_item_menu_node = None
                for key in (
                    "UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC", "/", "y",
                    "z", "r", "p", "o", "t", "a", "l", "i", "MENU", "k", "x", "h", "v", "g",
                ):
                    player.unbind_key(key)
                player.clear_overlay(overlay_id=_PLEX_ITEM_MENU_OVERLAY_ID)
                # Restore the full base Plex binding set -- same block
                # finish_plex_search_input/finish_plex_year_input restore,
                # and for the same reason (see finish_plex_search_input's
                # own comment).
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("p", toggle_live_pause)
                player.on_key_press("o", toggle_picture_in_picture)
                player.on_key_press("t", toggle_subtitles)
                player.on_key_press("a", toggle_about_overlay)
                player.on_key_press("l", toggle_plex_browser)
                player.on_key_press("i", show_vod_info_overlay)
                player.on_key_press("MENU", show_vod_info_overlay)
                player.on_key_press("k", toggle_chromecast_picker)
                player.on_key_press("x", toggle_history_browser)
                player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
                player.on_key_press("h", toggle_plex_favorite)
                player.on_key_press("v", toggle_plex_favorites_only)
                player.on_key_press("g", toggle_plex_grid_view)
                player.on_key_press("UP", plex_move_up)
                player.on_key_press("DOWN", plex_move_down)
                player.on_key_press("PGUP", plex_move_page_up)
                player.on_key_press("PGDWN", plex_move_page_down)
                player.on_key_press_or_hold("ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                player.on_key_press_or_hold("KP_ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                player.on_key_press("ESC", plex_back)
                player.on_key_press("LEFT", plex_move_left)
                player.on_key_press("RIGHT", plex_move_right)
                player.on_key_press("/", start_plex_search_input)
                player.on_key_press("y", start_plex_year_input)
                render_and_show_plex()

            def open_plex_item_menu() -> None:
                # The on_hold half of the ENTER/KP_ENTER tap-or-hold split
                # (see open_plex_browser/finish_plex_search_input/
                # finish_plex_year_input) -- a normal tap still plays/
                # drills in exactly as before via select_plex_node.
                nonlocal plex_item_menu_node, plex_item_menu_index
                if not plex_visible or not plex_nav_stack or plex_item_menu_node is not None:
                    return
                frame = plex_nav_stack[-1]
                nodes = plex_frame_nodes(frame)
                if not nodes:
                    return
                node = nodes[frame.selected_index]
                if node.kind not in _PLEX_ITEM_MENU_KINDS:
                    player.show_text("No actions for this item", duration_ms=2000)
                    return
                plex_item_menu_node = node
                plex_item_menu_index = 0
                for key in (
                    "UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC", "/", "y",
                    "z", "r", "p", "o", "t", "a", "l", "i", "MENU", "k", "x", "h", "v", "g",
                ):
                    player.unbind_key(key)
                player.on_key_press("UP", lambda: move_plex_item_menu_selection(-1))
                player.on_key_press("DOWN", lambda: move_plex_item_menu_selection(1))
                player.on_key_press("ENTER", activate_plex_item_menu_entry)
                player.on_key_press("KP_ENTER", activate_plex_item_menu_entry)
                player.on_key_press("ESC", close_plex_item_menu)
                player.on_key_press("LEFT", close_plex_item_menu)  # LEFT cancels, mirroring ESC
                render_and_show_plex_item_menu()
                logger.info("Plex item menu opened for '%s'", node.title)

            def _mark_plex_item_watched(node: PlexNode) -> None:
                player.show_text("Marking as watched...", duration_ms=2000)
                ok, error = mark_plex_watched(plex_creds, node.rating_key)
                if not ok:
                    player.show_text(f"Plex error: {error}", duration_ms=4000)
                    logger.error("Plex error marking '%s' watched: %s", node.title, error)
                    return
                # Instant local update after a successful round-trip,
                # rather than a full re-fetch, same shape
                # toggle_plex_favorite already uses (there just for local
                # file state instead of a network call) -- PlexNode is a
                # plain, unfrozen dataclass, so this is safe to mutate
                # in place.
                node.watched = True
                node.watch_progress = None
                player.show_text(f"Marked as watched: {node.title}", duration_ms=2000)
                logger.info("Marked as watched: '%s'", node.title)
                render_and_show_plex()

            def _mark_plex_item_unwatched(node: PlexNode) -> None:
                player.show_text("Marking as unwatched...", duration_ms=2000)
                ok, error = mark_plex_unwatched(plex_creds, node.rating_key)
                if not ok:
                    player.show_text(f"Plex error: {error}", duration_ms=4000)
                    logger.error("Plex error marking '%s' unwatched: %s", node.title, error)
                    return
                node.watched = False
                node.watch_progress = None
                player.show_text(f"Marked as unwatched: {node.title}", duration_ms=2000)
                logger.info("Marked as unwatched: '%s'", node.title)
                render_and_show_plex()

            def activate_plex_item_menu_entry() -> None:
                node = plex_item_menu_node
                if node is None:
                    return
                entry = _plex_item_menu_entries(node)[plex_item_menu_index]
                # Closed first so the action's own player.show_text isn't
                # immediately clobbered by the menu overlay still being on
                # screen, same ordering select_plex_node's leaf-item path
                # already relies on (close_plex_browser before its own
                # "Loading..."/"Playing: ..." messages).
                close_plex_item_menu()
                if entry == "Play from Start":
                    _play_plex_node(node, force_from_start=True)
                elif entry == "Mark as Watched":
                    _mark_plex_item_watched(node)
                elif entry == "Mark as Unwatched":
                    _mark_plex_item_unwatched(node)

            def stop_plex_playback_and_reopen_browser() -> None:
                # BS ("stop") in a Plex session drops back to browsing
                # instead of quitting tvdinner entirely -- overrides the
                # universal BS -> player.quit_playback binding (see the
                # top of this function), since Plex is the one session
                # type where "stop what's playing and pick something
                # else" is a genuinely useful, distinct action from
                # "quit the app": there's always a browser to fall back
                # into, and open_plex_browser reopens it exactly where
                # plex_nav_stack last left it, not back at the library
                # root. Reuses select_plex_node's own save-position/
                # history bookkeeping, just in reverse. Harmless to call
                # with nothing playing (player.stop() and
                # open_plex_browser() are both already idempotent). BS
                # isn't shadowed while the item menu is open (same as it
                # isn't during search/year text entry), so close that
                # first if it's up -- otherwise its own overlay and
                # keybindings would be left stranded behind whatever
                # open_plex_browser rebinds below.
                nonlocal playing_recording, playing_vod_item
                if plex_item_menu_node is not None:
                    close_plex_item_menu()
                _save_current_recording_position()
                _save_current_vod_position()
                _report_plex_state("stopped")
                _end_current_history_entry()
                _reset_reconnect_state()
                playing_recording = None
                playing_vod_item = None
                player.stop()
                open_plex_browser()

            def plex_go_back() -> None:
                # Overrides the universal GO_BACK -> synthesize("ESC")
                # binding (see the top of this function) for the one case
                # that binding gets wrong in a Plex session: with nothing
                # open (just watching), plain ESC has no meaning of its
                # own here, so it falls through to mpv's own default
                # binding (cycle fullscreen/window mode) -- confirmed live,
                # and not what a "back" button should do while playing.
                # Whenever there's actually something to back out of
                # (the browser itself, or any of the overlays that can be
                # open on top of playback -- help/about/history/
                # Chromecast picker/update notice), synthesizing ESC is
                # still exactly right and needs no duplicating here, same
                # reasoning as the universal binding's own comment.
                # Otherwise, GO_BACK acts like BS: stop the current item
                # and drop back to browsing.
                if (
                    plex_visible
                    or help_visible
                    or about_visible
                    or history_browser_visible
                    or chromecast_visible
                    or update_notice_visible
                ):
                    player.synthesize_key_press("ESC")
                else:
                    stop_plex_playback_and_reopen_browser()

            def plex_back() -> None:
                if not plex_visible:
                    return
                if len(plex_nav_stack) > 1:
                    plex_nav_stack.pop()
                    render_and_show_plex()
                else:
                    close_plex_browser()

            def open_plex_browser() -> None:
                nonlocal plex_visible
                if not plex_nav_stack:
                    plex_nav_stack.append(_PlexNavFrame(breadcrumb="Plex Libraries", nodes=list(plex_root_nodes or [])))
                if render_and_show_plex():
                    plex_visible = True
                    player.on_key_press("UP", plex_move_up)
                    player.on_key_press("DOWN", plex_move_down)
                    player.on_key_press("PGUP", plex_move_page_up)
                    player.on_key_press("PGDWN", plex_move_page_down)
                    player.on_key_press_or_hold("ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                    player.on_key_press_or_hold("KP_ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                    player.on_key_press("ESC", plex_back)
                    player.on_key_press("LEFT", plex_move_left)  # back a level in list view, previous column in grid view
                    player.on_key_press("RIGHT", plex_move_right)  # next column in grid view, unbound in list view
                    player.on_key_press("/", start_plex_search_input)
                    player.on_key_press("y", start_plex_year_input)
                    logger.info("Plex browser opened")

            def toggle_plex_browser() -> None:
                if plex_visible:
                    close_plex_browser()
                    return
                if help_visible:
                    close_help_overlay()
                if about_visible:
                    close_about_overlay()
                if history_browser_visible:
                    close_history_browser()
                open_plex_browser()

            def render_plex_search_prompt() -> None:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_guide_filter_prompt(plex_search_text, osd_size[0], osd_size[1], label="Search Plex library")
                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_PLEX_SEARCH_OVERLAY_ID)

            def append_plex_search_char(char: str) -> None:
                nonlocal plex_search_text
                plex_search_text += char
                render_plex_search_prompt()

            def remove_plex_search_char() -> None:
                nonlocal plex_search_text
                plex_search_text = plex_search_text[:-1]
                render_plex_search_prompt()

            def finish_plex_search_input() -> None:
                nonlocal plex_search_input_active
                plex_search_input_active = False
                for char in _FILTER_INPUT_CHARS:
                    player.unbind_key(char)
                player.unbind_key("SPACE")
                player.unbind_key("BS")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.unbind_key("LEFT")
                player.clear_overlay(overlay_id=_PLEX_SEARCH_OVERLAY_ID)
                # Restore the always-on bindings the a-z rebind shadowed --
                # for a Plex-only session that's just the top-of-play_stream
                # keys plus 'l'/'i'/'k'/'j'/'x', since a Plex session never
                # defines the guide's own g/h/w/u/m/b bindings at all (see
                # the comment on the sibling "if channel is not None" block
                # above).
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("p", toggle_live_pause)
                player.on_key_press("o", toggle_picture_in_picture)
                player.on_key_press("t", toggle_subtitles)
                player.on_key_press("a", toggle_about_overlay)
                player.on_key_press("l", toggle_plex_browser)
                player.on_key_press("i", show_vod_info_overlay)
                player.on_key_press("MENU", show_vod_info_overlay)
                player.on_key_press("k", toggle_chromecast_picker)
                player.on_key_press("x", toggle_history_browser)
                player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
                player.on_key_press("h", toggle_plex_favorite)
                player.on_key_press("v", toggle_plex_favorites_only)
                player.on_key_press("g", toggle_plex_grid_view)
                player.on_key_press("UP", plex_move_up)
                player.on_key_press("DOWN", plex_move_down)
                player.on_key_press("PGUP", plex_move_page_up)
                player.on_key_press("PGDWN", plex_move_page_down)
                player.on_key_press_or_hold("ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                player.on_key_press_or_hold("KP_ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                player.on_key_press("ESC", plex_back)
                player.on_key_press("LEFT", plex_move_left)  # back a level in list view, previous column in grid view
                player.on_key_press("RIGHT", plex_move_right)  # next column in grid view, unbound in list view
                player.on_key_press("/", start_plex_search_input)
                player.on_key_press("y", start_plex_year_input)
                render_and_show_plex()

            def confirm_plex_search() -> None:
                query = plex_search_text.strip()
                finish_plex_search_input()
                if not query:
                    return
                player.show_text(f"Searching for '{query}'...", duration_ms=2000)
                results, error = search_plex(plex_creds, query)
                if error:
                    player.show_text(f"Plex search error: {error}", duration_ms=4000)
                    logger.error("Plex search error: %s", error)
                    return
                if not results:
                    player.show_text(f"No results for '{query}'", duration_ms=3000)
                    return
                plex_nav_stack.append(_PlexNavFrame(breadcrumb=f"Search: {query}", nodes=results))
                render_and_show_plex()
                logger.info("Plex search '%s' -> %d results", query, len(results))

            def cancel_plex_search() -> None:
                finish_plex_search_input()
                logger.info("Plex search input cancelled")

            def start_plex_search_input() -> None:
                nonlocal plex_search_input_active, plex_search_text
                if not plex_visible or plex_search_input_active:
                    return
                plex_search_input_active = True
                plex_search_text = ""
                # MENU isn't a letter, so unlike 'i' it's not incidentally
                # shadowed by the a-z rebind just below -- unbound
                # explicitly here instead, restored by
                # finish_plex_search_input like everything else.
                for key in ("UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC", "/", "MENU"):
                    player.unbind_key(key)
                for char in _FILTER_INPUT_CHARS:
                    player.on_key_press(char, lambda char=char: append_plex_search_char(char))
                player.on_key_press("SPACE", lambda: append_plex_search_char(" "))
                player.on_key_press("BS", remove_plex_search_char)
                player.on_key_press("ENTER", confirm_plex_search)
                player.on_key_press("KP_ENTER", confirm_plex_search)
                player.on_key_press("ESC", cancel_plex_search)
                player.on_key_press("LEFT", cancel_plex_search)  # LEFT cancels search input, mirroring ESC
                render_plex_search_prompt()
                logger.info("Plex search input started")

            def render_plex_year_prompt() -> None:
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_guide_filter_prompt(plex_year_text, osd_size[0], osd_size[1], label="Filter by release year")
                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_PLEX_YEAR_OVERLAY_ID)

            def append_plex_year_char(char: str) -> None:
                nonlocal plex_year_text
                if len(plex_year_text) >= _YEAR_INPUT_MAX_DIGITS:
                    return
                plex_year_text += char
                render_plex_year_prompt()

            def remove_plex_year_char() -> None:
                nonlocal plex_year_text
                plex_year_text = plex_year_text[:-1]
                render_plex_year_prompt()

            def finish_plex_year_input() -> None:
                nonlocal plex_year_input_active
                plex_year_input_active = False
                for char in _YEAR_INPUT_CHARS:
                    player.unbind_key(char)
                player.unbind_key("BS")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.unbind_key("LEFT")
                player.clear_overlay(overlay_id=_PLEX_YEAR_OVERLAY_ID)
                # Restore the always-on bindings the digit-key rebind
                # shadowed -- same full set finish_plex_search_input
                # restores, and for the same reason (see its own comment).
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("p", toggle_live_pause)
                player.on_key_press("o", toggle_picture_in_picture)
                player.on_key_press("t", toggle_subtitles)
                player.on_key_press("a", toggle_about_overlay)
                player.on_key_press("l", toggle_plex_browser)
                player.on_key_press("i", show_vod_info_overlay)
                player.on_key_press("MENU", show_vod_info_overlay)
                player.on_key_press("k", toggle_chromecast_picker)
                player.on_key_press("x", toggle_history_browser)
                player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
                player.on_key_press("h", toggle_plex_favorite)
                player.on_key_press("v", toggle_plex_favorites_only)
                player.on_key_press("g", toggle_plex_grid_view)
                player.on_key_press("UP", plex_move_up)
                player.on_key_press("DOWN", plex_move_down)
                player.on_key_press("PGUP", plex_move_page_up)
                player.on_key_press("PGDWN", plex_move_page_down)
                player.on_key_press_or_hold("ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                player.on_key_press_or_hold("KP_ENTER", on_press=select_plex_node, on_hold=open_plex_item_menu)
                player.on_key_press("ESC", plex_back)
                player.on_key_press("LEFT", plex_move_left)
                player.on_key_press("RIGHT", plex_move_right)
                player.on_key_press("/", start_plex_search_input)
                player.on_key_press("y", start_plex_year_input)
                render_and_show_plex()

            def confirm_plex_year() -> None:
                year = plex_year_text.strip()
                finish_plex_year_input()
                if not year:
                    return
                player.show_text(f"Finding {year} releases...", duration_ms=2000)
                results, error = search_plex_by_year(plex_creds, year)
                if error:
                    player.show_text(f"Plex error: {error}", duration_ms=4000)
                    logger.error("Plex year filter error: %s", error)
                    return
                if not results:
                    player.show_text(f"No {year} releases found", duration_ms=3000)
                    return
                plex_nav_stack.append(_PlexNavFrame(breadcrumb=f"{year} releases", nodes=results))
                render_and_show_plex()
                logger.info("Plex year filter '%s' -> %d results", year, len(results))

            def cancel_plex_year_input() -> None:
                finish_plex_year_input()
                logger.info("Plex year filter input cancelled")

            def start_plex_year_input() -> None:
                nonlocal plex_year_input_active, plex_year_text
                if not plex_visible or plex_year_input_active:
                    return
                plex_year_input_active = True
                plex_year_text = ""
                # Unlike start_plex_search_input, whose a-z character set
                # incidentally shadows every top-level single-letter Plex
                # binding (z/r/p/o/t/a/l/i/k/x/y) as a side effect, year
                # input's digit-only set doesn't overlap with any of them
                # at all -- explicitly unbinding all of them here (not
                # just the nav keys) gets the same "nothing else can fire
                # while this prompt is up" guarantee finish_plex_year_input
                # already restores afterward.
                for key in (
                    "UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC", "/", "y",
                    "z", "r", "p", "o", "t", "a", "l", "i", "MENU", "k", "x", "h", "v", "g",
                ):
                    player.unbind_key(key)
                for char in _YEAR_INPUT_CHARS:
                    player.on_key_press(char, lambda char=char: append_plex_year_char(char))
                player.on_key_press("BS", remove_plex_year_char)
                player.on_key_press("ENTER", confirm_plex_year)
                player.on_key_press("KP_ENTER", confirm_plex_year)
                player.on_key_press("ESC", cancel_plex_year_input)
                player.on_key_press("LEFT", cancel_plex_year_input)  # LEFT cancels, mirroring ESC
                render_plex_year_prompt()
                logger.info("Plex year filter input started")

            player.on_key_press("l", toggle_plex_browser)  # 'l' (library) browses the Plex library
            player.on_key_press("i", show_vod_info_overlay)  # 'i' shows info for whatever's currently playing
            # MENU has no guide to fall back to here (Plex has no live-
            # channel/EPG concept at all) -- unlike the channel session's
            # tap/hold split, it's simply a permanent alias for 'i'.
            player.on_key_press("MENU", show_vod_info_overlay)
            # Overrides the universal BS -> player.quit_playback binding
            # (see the top of this function) -- in a Plex session, "stop"
            # means stop the current item and drop back to browsing, not
            # quit tvdinner entirely, since there's always a browser to
            # fall back into.
            player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
            player.on_key_press("h", toggle_plex_favorite)  # 'h' (heart) favorites the selected movie/show
            player.on_key_press("v", toggle_plex_favorites_only)  # favorites-only view, same key as the guide's
            player.on_key_press("g", toggle_plex_grid_view)  # switch between list/grid view, persists until toggled back
            # Overrides the universal GO_BACK -> synthesize("ESC") binding
            # (see the top of this function) -- see plex_go_back's own
            # comment for why.
            player.on_key_press("GO_BACK", plex_go_back)
            open_plex_browser()

        player.wait_for_playback()
    except KeyboardInterrupt:
        logger.info("Interrupted (Ctrl-C)")
    finally:
        # An impatient second Ctrl-C landing anywhere in this cleanup
        # (confirmed live via a user report: it landed inside
        # player.quit()'s own mpv.terminate() call) used to propagate as
        # an unhandled crash instead of a clean exit -- the first
        # Ctrl-C's KeyboardInterrupt is already caught by the except
        # clause above, but nothing protected the *cleanup* that runs
        # afterward. Wrapping it here, with player.quit() pulled out into
        # its own nested finally, means a second interrupt is logged and
        # swallowed rather than crashing, while still guaranteeing
        # player.quit() actually runs either way -- otherwise a second
        # Ctrl-C landing before reaching it could leave mpv running as an
        # orphaned process.
        try:
            cancel_hide_timer()
            cancel_resize_timer()
            cancel_guide_logo_refresh_timer()
            cancel_history_image_refresh_timer()
            cancel_live_pause_timer()
            cancel_reconnect_timer()
            cancel_reconnect_stability_timer()
            if chromecast_stop_discovery is not None:
                chromecast_stop_discovery()
            try:
                # Player.playback_position() already treats mpv's core being
                # mid-shutdown (e.g. the user quit via its own default 'q') as
                # "not available" rather than raising -- this is just a last
                # line of defense so a genuinely unexpected error here can
                # never skip player.quit() below.
                _save_current_recording_position()
                _save_current_vod_position()
                # Synchronous (background=False), not threaded like every
                # other call site -- the process is exiting right after
                # this block, so a backgrounded thread might never
                # actually get to run.
                _report_plex_state("stopped", background=False)
                _end_current_history_entry()
            except Exception:
                logger.exception("Could not save playback position on shutdown")
            playback_autosave_stop_event.set()
            schedule_stop_event.set()
        except KeyboardInterrupt:
            logger.info("Interrupted again during shutdown -- finishing cleanup")
        finally:
            try:
                player.quit()
            except KeyboardInterrupt:
                logger.info("Interrupted again while closing mpv -- exiting anyway")
        logger.info("Shutting down")
    return 0


class _EpilogRawHelpFormatter(argparse.HelpFormatter):
    """Like the default formatter (wraps `description` and every
    argument's help text to the terminal width) except for `epilog`,
    which is instead preserved exactly as written -- argparse's own
    RawDescriptionHelpFormatter stops *all* wrapping, including
    `description`'s, which is a real regression for one this long
    (confirmed live: it prints as one giant unwrapped line without
    this). _fill_text is called for both fields with no way to tell
    which one from its own arguments, so this distinguishes them by
    the epilog's own known leading text instead."""

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        if text.startswith("commands:"):
            return "".join(indent + line for line in text.splitlines(keepends=True))
        return super()._fill_text(text, width, indent)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tvdinner",
        description="Play IPTV streams from an M3U playlist, an Xtream Codes login "
        "(xtream://username:password@host:port), a Stalker Portal login "
        "(stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF), an HDHomeRun tuner "
        "(hdhomerun://host[:port]), a Plex Media Server login "
        "(plex://host:port?X-Plex-Token=...), a direct stream URL, a local video file, or a "
        "YouTube video URL (a local file's movie identity is guessed from its filename, a "
        "YouTube video's from its own title -- either way see --title/--year/--tmdb-api-token "
        "for the 'i' overlay).",
        # These aren't real argparse subparsers (see main()'s own
        # raw_argv[:1] == [...] dispatch, ahead of build_parser().
        # parse_args() -- a genuine subparsers object would force every
        # invocation to name a subcommand explicitly, losing plain
        # `tvdinner URL` as the default/bare form), so argparse never
        # lists them on its own -- spelled out here instead, or `--help`
        # alone would give no hint any of this exists. RawDescriptionHelp
        # Formatter keeps this block's own line breaks/indentation as
        # written, instead of argparse rewrapping it into one paragraph
        # the way `description` above is.
        epilog="commands:\n"
        "  tvdinner                         same as 'tvdinner bookmarks' (no URL given)\n"
        "  tvdinner bookmarks               manage and launch saved playlist bookmarks\n"
        "  tvdinner backup [PATH]           save configuration to a single archive\n"
        "  tvdinner restore [PATH]          restore configuration from a backup archive\n"
        "  tvdinner gdrive-login            sign in to Google Drive for --gdrive backups\n"
        "  tvdinner gdrive-logout           forget the stored Google Drive sign-in\n"
        "  tvdinner stats                   show on-disk cache usage\n"
        "  tvdinner store-tmdb TOKEN        save a default TMDB API token\n"
        "  tvdinner clear-tmdb              remove the stored default TMDB API token\n"
        "  tvdinner hard-reset              delete all stored data and start fresh\n"
        "\n"
        "Run 'tvdinner <command> --help' for a command's own options.",
        formatter_class=_EpilogRawHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "url",
        help="M3U/M3U8 playlist URL or local file path, an Xtream Codes login "
        "(xtream://username:password@host:port, or xtreams:// for https), a Stalker Portal "
        "login (stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF, or stalkers:// for "
        "https), an HDHomeRun tuner (hdhomerun://host[:port]), a Plex Media Server login "
        "(plex://host:port?X-Plex-Token=..., or plexs:// for https), a direct video/audio "
        "stream URL, a local video file to play directly (e.g. a movie -- anything that isn't "
        "itself an M3U playlist), or a youtube.com/youtu.be video URL",
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
        "--vod-group",
        metavar="GROUP",
        action="append",
        help="An M3U group-title (exact match) to pull out of the guide/channel list and into "
        "the VOD movie browser (see the 'm' keybinding) instead -- repeat to name several "
        "groups. Only affects plain M3U/local playlists; Xtream and Stalker panels expose VOD "
        "as a separate API and are always browsed this way when present. Has no effect by "
        "default, so existing M3U playlists behave exactly as before unless you opt a group in.",
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
        "--disable-full-screen",
        action="store_true",
        help="Start in a normal window instead of full screen (the default)",
    )
    parser.add_argument(
        "--glsl-shader",
        metavar="PATH",
        action="append",
        help="A custom GLSL shader file (e.g. an Anime4K or FSRCNNX shader) to apply on top of "
        "mpv's own built-in scalers (see --profile=gpu-hq, always on) -- repeat to layer "
        "several, applied in the order given. Off by default: these can be significantly "
        "heavier on the GPU than the built-in scalers alone, so it's opt-in per shader rather "
        "than bundled or guessed at.",
    )
    parser.add_argument(
        "--interpolation",
        action="store_true",
        help="Smooth motion by interpolating between frames (mpv's interpolation + "
        "video-sync=display-resample) -- only actually helps when the display's refresh rate "
        "is a clean multiple of the video's frame rate, adds GPU cost, and switches how mpv "
        "times playback against audio, so it's off by default rather than applied globally "
        "alongside --profile=gpu-hq.",
    )
    parser.add_argument(
        "--playback-positions-file",
        metavar="PATH",
        help="JSON file remembering where you left off in each recording (see the 'w' "
        f"recordings browser), so reopening one resumes instead of starting over (default: "
        f"{DEFAULT_PLAYBACK_POSITIONS_PATH})",
    )
    parser.add_argument(
        "--history-file",
        metavar="PATH",
        help="JSONL file logging what's watched (channel/VOD/recording), when, and for how "
        f"long -- browse it with the 'x' keybinding (default: {DEFAULT_HISTORY_PATH})",
    )
    parser.add_argument("--no-history", action="store_true", help="Don't record watch history")
    parser.add_argument(
        "--no-plex-activity",
        action="store_true",
        help="Don't report playback to the Plex server (Plex source only) -- on by default, "
        "this is what makes tvdinner playback show up in Plex's own dashboard and in "
        "third-party tools like Tautulli, and lets Plex update its own watched status/resume "
        "position for the item. Reading Plex's own watched/resume status is unaffected either way",
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
        "--no-online-logos",
        action="store_true",
        help="Don't fall back to iptv-org's community channel/logo database "
        "(https://github.com/iptv-org/api) for channels with no logo of their own or in "
        "their EPG -- common for bare M3U playlists. On by default; shares --epg-cache-hours/"
        "--no-epg-cache/--refresh-epg-cache's caching",
    )
    parser.add_argument(
        "--tmdb-api-token",
        metavar="TOKEN",
        help="TMDB v4 read-access Bearer token -- enables a gold star rating (e.g. '★ 7.6') "
        "plus the required 'TMDB' attribution mark and director credit (when TMDB has one) on "
        "movie programmes in the guide grid and details popup. Movies only, matched by "
        "programme category. Ratings are fetched in the background (never blocking guide "
        "rendering) and cached on disk for "
        f"{DEFAULT_TMDB_CACHE_MAX_AGE.days} days. Off by default; overrides any token saved via "
        "'tvdinner store-tmdb'. For a local video file or YouTube URL, this instead enables the "
        "'i' overlay's poster/synopsis/rating/director, looked up by its guessed (or "
        "--title/--year overridden) identity",
    )
    parser.add_argument(
        "--tmdb-token-file",
        metavar="PATH",
        help=f"Where 'tvdinner store-tmdb' saves its default token (default: {DEFAULT_TMDB_TOKEN_PATH})",
    )
    parser.add_argument(
        "--title",
        metavar="TITLE",
        help="Local video file or YouTube URL playback only: override the guessed movie title "
        "used for the --tmdb-api-token lookup",
    )
    parser.add_argument(
        "--year",
        metavar="YEAR",
        help="Local video file or YouTube URL playback only: override the guessed release year "
        "used for the --tmdb-api-token lookup",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Don't check GitHub Releases for a newer tvdinner version at startup (on by default, "
        "at most once every 24 hours; approving or dismissing a notice never nags about that "
        "same version again)",
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
    if selected.tmdb_api_token:
        bookmark_argv += ["--tmdb-api-token", selected.tmdb_api_token]
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
    # Never logs selected.tmdb_api_token itself -- same redact-before-
    # logging norm as every other credential in this codebase.
    logged_argv = [redact_plex_url(redact_stalker_url(redact_xtream_url(bookmark_argv[0])))]
    for arg in bookmark_argv[1:]:
        logged_argv.append("***" if arg == selected.tmdb_api_token else arg)
    logger.info("Launching bookmark '%s': %s", selected.name, logged_argv)
    return main(bookmark_argv)


def _add_config_path_args(parser: argparse.ArgumentParser) -> None:
    """--epg-shifts/--favorites/--bookmarks-file/--tmdb-token-file
    overrides shared by `backup`, `restore`, and `hard-reset`, so a
    backup made from custom paths restores to the same custom paths."""
    parser.add_argument(
        "--epg-shifts", metavar="PATH", help=f"EPG shifts file (default: {DEFAULT_CHANNEL_SHIFTS_PATH})"
    )
    parser.add_argument("--favorites", metavar="PATH", help=f"Favorites file (default: {DEFAULT_FAVORITES_PATH})")
    parser.add_argument(
        "--bookmarks-file", metavar="PATH", help=f"Bookmarks file (default: {DEFAULT_BOOKMARKS_PATH})"
    )
    parser.add_argument(
        "--tmdb-token-file",
        metavar="PATH",
        help=f"Stored default TMDB API token file (default: {DEFAULT_TMDB_TOKEN_PATH})",
    )


def _config_paths(args: argparse.Namespace) -> dict[str, Path]:
    return {
        "epg_shifts.json": Path(args.epg_shifts) if args.epg_shifts else DEFAULT_CHANNEL_SHIFTS_PATH,
        "favorites.json": Path(args.favorites) if args.favorites else DEFAULT_FAVORITES_PATH,
        "bookmarks.json": Path(args.bookmarks_file) if args.bookmarks_file else DEFAULT_BOOKMARKS_PATH,
        "tmdb_token.json": Path(args.tmdb_token_file) if args.tmdb_token_file else DEFAULT_TMDB_TOKEN_PATH,
    }


def _add_gdrive_args(parser: argparse.ArgumentParser, *, gdrive_help: str) -> None:
    parser.add_argument("--gdrive", action="store_true", help=gdrive_help)
    parser.add_argument(
        "--gdrive-filename",
        metavar="NAME",
        default=DEFAULT_GDRIVE_BACKUP_NAME,
        help=f"Name of the backup file in Google Drive (default: {DEFAULT_GDRIVE_BACKUP_NAME})",
    )
    parser.add_argument(
        "--gdrive-token-file",
        metavar="PATH",
        help=f"Where 'tvdinner gdrive-login' stored its sign-in (default: {DEFAULT_GDRIVE_TOKEN_PATH})",
    )


def _load_gdrive_credentials_for_command(args: argparse.Namespace) -> dict[str, str] | None:
    """Load stored Drive credentials for --gdrive, printing/logging a
    "not signed in" error and returning None if there aren't any --
    callers should treat None as "return 1", not raise/exit directly, to
    stay consistent with every other run_*_command's error handling."""
    token_path = Path(args.gdrive_token_file) if args.gdrive_token_file else DEFAULT_GDRIVE_TOKEN_PATH
    credentials, warnings = load_gdrive_credentials(token_path)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)
    if credentials is None:
        print(
            f"Not signed in to Google Drive ({token_path} not found). Run 'tvdinner gdrive-login' first.",
            file=sys.stderr,
        )
        logger.error("Google Drive backup/restore requested but not signed in (%s)", token_path)
    return credentials


def run_backup_command(argv: list[str]) -> int:
    """Handle `tvdinner backup [PATH]`: write EPG shifts, favorites,
    bookmarks, and a stored default TMDB token into a single zip archive
    for offline storage or moving to another machine. The EPG cache and
    log file are deliberately left out -- they're disposable, not
    configuration. With --gdrive, also uploads the archive to Google
    Drive (see 'tvdinner gdrive-login')."""
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
    _add_gdrive_args(parser, gdrive_help="Also upload the backup to Google Drive")
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log startup/shutdown, user actions, and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--no-log", action="store_true", help="Disable file logging entirely")
    args = parser.parse_args(argv)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)

    gdrive_credentials = None
    if args.gdrive:
        gdrive_credentials = _load_gdrive_credentials_for_command(args)
        if gdrive_credentials is None:
            return 1

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

    if gdrive_credentials is not None:
        try:
            upload_gdrive_backup(gdrive_credentials, args.gdrive_filename, output_path.read_bytes())
        except (GdriveError, OSError) as exc:
            print(f"Could not upload backup to Google Drive: {exc}", file=sys.stderr)
            logger.error("Could not upload backup to Google Drive: %s", exc)
            return 1
        print(f"Uploaded to Google Drive as '{args.gdrive_filename}'.")
        logger.info("Uploaded backup to Google Drive as '%s'", args.gdrive_filename)
    return 0


def run_restore_command(argv: list[str]) -> int:
    """Handle `tvdinner restore PATH`: extract EPG shifts, favorites,
    bookmarks, and a stored default TMDB token from a backup archive,
    overwriting the current ones. Prompts for confirmation unless
    -y/--yes is given, since this replaces existing configuration. With
    --gdrive, downloads the archive from Google Drive instead of reading
    a local PATH (see 'tvdinner gdrive-login')."""
    parser = argparse.ArgumentParser(
        prog="tvdinner restore",
        description="Restore tvdinner's configuration files from a backup archive, overwriting the current ones.",
    )
    parser.add_argument(
        "input", nargs="?", metavar="PATH", help="Backup archive to restore from (omit when using --gdrive)"
    )
    _add_config_path_args(parser)
    _add_gdrive_args(parser, gdrive_help="Restore from Google Drive instead of a local PATH")
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

    if not args.gdrive and not args.input:
        parser.error("the following arguments are required: PATH (unless --gdrive is given)")

    gdrive_credentials = None
    if args.gdrive:
        gdrive_credentials = _load_gdrive_credentials_for_command(args)
        if gdrive_credentials is None:
            return 1
    config_paths = _config_paths(args)

    downloaded_path: Path | None = None
    if gdrive_credentials is not None:
        try:
            data = download_gdrive_backup(gdrive_credentials, args.gdrive_filename)
        except GdriveError as exc:
            print(f"Could not download backup from Google Drive: {exc}", file=sys.stderr)
            logger.error("Could not download backup from Google Drive: %s", exc)
            return 1
        fd, downloaded_name = tempfile.mkstemp(prefix="tvdinner-gdrive-restore-", suffix=".zip")
        downloaded_path = Path(downloaded_name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        input_path = downloaded_path
        logger.info("Downloaded backup from Google Drive as '%s'", args.gdrive_filename)
    else:
        input_path = Path(args.input)

    logger.info("Starting tvdinner %s restore <- %s", __version__, input_path)

    if not args.yes:
        source = f"Google Drive ('{args.gdrive_filename}')" if gdrive_credentials is not None else str(input_path)
        answer = input(
            f"This will overwrite tvdinner's current configuration files with the contents of "
            f"{source}. Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Restore cancelled.")
            logger.info("Restore cancelled by user")
            if downloaded_path is not None:
                downloaded_path.unlink(missing_ok=True)
            return 0

    try:
        restored, unknown = restore_backup(input_path, config_paths)
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"Could not restore from {input_path}: {exc}", file=sys.stderr)
        logger.error("Could not restore from %s: %s", input_path, exc)
        return 1
    finally:
        if downloaded_path is not None:
            downloaded_path.unlink(missing_ok=True)

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


def _format_cache_bytes(size_bytes: int) -> str:
    """Same unit-stepping as overlay.py's own _format_size (used for a
    recording's byte size in the 'w' browser) -- duplicated rather than
    imported, since that one lives in a module about rendering OSD
    overlay images, not plain stdout text."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # unreachable, but keeps type checkers happy


def _dir_size(path: Path) -> int:
    """Total size of every regular file directly or indirectly under
    `path`, or 0 if it doesn't exist yet -- a cache directory that's
    never been written to (e.g. TMDB caching never used) isn't an
    error."""
    if not path.is_dir():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _log_total_size(log_path: Path) -> int:
    """Total bytes across the live log file and any rotated backups
    RotatingFileHandler left alongside it (tvdinner.log, tvdinner.log.1,
    ...) -- log.py caps the live file at 5MB with one backup, so a plain
    stat() on log_path alone would silently miss half the on-disk cost."""
    total = log_path.stat().st_size if log_path.is_file() else 0
    total += sum(f.stat().st_size for f in log_path.parent.glob(f"{log_path.name}.*") if f.is_file())
    return total


def _print_stats_table(
    headers: list[str], rows: list[list[str]], right_align: set[int], file=None
) -> None:
    """A minimal, dependency-free aligned text table -- headers/rows are
    already-formatted strings; `right_align` names which column indices
    (0-based) to right- rather than left-justify (the byte-size
    columns). `file` defaults to sys.stdout looked up at call time, not
    def time -- a bare `file=sys.stdout` default binds whatever stdout
    object existed at import, which silently stops going to a
    since-redirected stdout (confirmed live: pytest's capsys fixture,
    which swaps sys.stdout out per test)."""
    if file is None:
        file = sys.stdout
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def format_row(cells: list[str]) -> str:
        justified = (cell.rjust(widths[i]) if i in right_align else cell.ljust(widths[i]) for i, cell in enumerate(cells))
        return "  ".join(justified).rstrip()

    print(format_row(headers), file=file)
    print("  ".join("-" * width for width in widths), file=file)
    for row in rows:
        print(format_row(row), file=file)


def run_stats_command(argv: list[str]) -> int:
    """Handle `tvdinner stats`: on-disk cache usage, broken down per
    bookmarked feed's EPG cache where its source is deterministically
    knowable without a network fetch (an explicit --epg override, or an
    Xtream login's own xmltv.php URL -- see xtream.xtream_epg_url), plus
    the caches every feed shares regardless of source (TMDB
    ratings/metadata, channel logos/poster art, iptv-org's online
    channel/logo database) and the log/history files, neither of which
    are really "caches" (nothing repopulates them from a network source)
    but get the same size/location treatment since they're the other
    ever-growing files on disk worth knowing about. A bookmark relying on
    M3U auto-discovery (x-tvg-url, requiring an actual playlist fetch to
    resolve) or with no EPG at all (Stalker, HDHomeRun without a DVR
    subscription, Plex) is listed as unknown rather than guessed."""
    parser = argparse.ArgumentParser(
        prog="tvdinner stats",
        description="Show on-disk cache usage: per bookmarked feed's EPG cache where knowable, "
        "plus the TMDB/image/online-logo caches every feed shares, and the log/history files.",
    )
    parser.add_argument(
        "--bookmarks-file", metavar="PATH", help=f"JSON file storing bookmarks (default: {DEFAULT_BOOKMARKS_PATH})"
    )
    parser.add_argument(
        "--history-file",
        metavar="PATH",
        help=f"JSONL file logging watch history (default: {DEFAULT_HISTORY_PATH})",
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
    # Independent of --no-log above (which only controls whether *this*
    # run writes new lines) -- the log file this reports on is whatever
    # accumulated from every *other* invocation that didn't pass it, so
    # --log-file is still honored as a path override but --no-log isn't
    # treated as "pretend it doesn't exist".
    report_log_path = Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH
    history_path = Path(args.history_file) if args.history_file else DEFAULT_HISTORY_PATH

    bookmarks_path = Path(args.bookmarks_file) if args.bookmarks_file else DEFAULT_BOOKMARKS_PATH
    bookmarks, warnings = load_bookmarks(bookmarks_path)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)
    logger.info("Starting tvdinner %s stats (bookmarks_file=%s)", __version__, bookmarks_path)

    # Every EPG cache file this loop can actually attribute to a bookmark
    # -- subtracted from the EPG cache directory's own total further down
    # to get an "other" bucket for feeds that aren't bookmarked at all
    # (a bare URL typed directly, or a bookmark since deleted) rather
    # than silently omitting that disk usage from the totals.
    identified_paths: set[Path] = set()
    sized_feeds: list[tuple[str, int]] = []
    unknown_feeds: list[str] = []

    for bookmark in bookmarks:
        epg_source = bookmark.epg
        if not epg_source and is_xtream_url(bookmark.url):
            creds = parse_xtream_url(bookmark.url)
            if creds is not None:
                epg_source = xtream_epg_url(creds)
        if not epg_source:
            unknown_feeds.append(bookmark.name)
            continue

        size = 0
        for path in (
            cache_path_for(DEFAULT_EPG_CACHE_DIR, epg_source, suffix=".xml"),
            parsed_cache_path_for(DEFAULT_EPG_CACHE_DIR, epg_source),
        ):
            if path.is_file():
                size += path.stat().st_size
                identified_paths.add(path)
        sized_feeds.append((bookmark.name, size))

    # Largest first -- the whole point of this table is "what's using my
    # disk", so that's the order that answers it fastest.
    sized_feeds.sort(key=lambda feed: feed[1], reverse=True)
    feed_rows = [[name, _format_cache_bytes(size) if size else "not cached yet"] for name, size in sized_feeds]
    feed_rows += [[name, "unknown (auto-discovered EPG)"] for name in sorted(unknown_feeds, key=str.lower)]

    print("Per-feed EPG cache (bookmarked feeds only):\n")
    if feed_rows:
        _print_stats_table(["Feed", "EPG Cache"], feed_rows, right_align={1})
    else:
        print("No bookmarks saved -- see 'tvdinner bookmarks'.")

    # The online channel/logo database (see channel_logos.py) shares the
    # EPG cache directory with per-feed EPG data (its own cache_dir
    # default), but is a fixed, global download (two well-known URLs),
    # not a per-feed one -- identified and excluded from "other" below
    # the same way a bookmarked feed's own EPG files are.
    online_logo_size = 0
    for url in (CHANNELS_URL, LOGOS_URL):
        path = cache_path_for(DEFAULT_EPG_CACHE_DIR, url, suffix=".json")
        if path.is_file():
            online_logo_size += path.stat().st_size
            identified_paths.add(path)

    epg_dir_total = _dir_size(DEFAULT_EPG_CACHE_DIR)
    identified_total = sum(path.stat().st_size for path in identified_paths if path.is_file())
    other_epg_size = max(0, epg_dir_total - identified_total)

    tmdb_size = _dir_size(DEFAULT_TMDB_CACHE_DIR)
    image_size = _dir_size(DEFAULT_IMAGE_CACHE_DIR)
    log_size = _log_total_size(report_log_path)
    history_size = history_path.stat().st_size if history_path.is_file() else 0
    grand_total = epg_dir_total + tmdb_size + image_size + log_size + history_size

    shared_rows = [
        ["TMDB ratings & metadata", _format_cache_bytes(tmdb_size)],
        ["Channel logos & poster art", _format_cache_bytes(image_size)],
        ["Online channel/logo database", _format_cache_bytes(online_logo_size)],
        ["Other EPG cache (unbookmarked feeds)", _format_cache_bytes(other_epg_size)],
        ["Log file", _format_cache_bytes(log_size)],
        ["Watch history", _format_cache_bytes(history_size)],
        ["Total", _format_cache_bytes(grand_total)],
    ]
    print("\nShared caches (used by every feed, not just bookmarked ones):\n")
    _print_stats_table(["Cache", "Size"], shared_rows, right_align={1})

    print("\nCache directories:")
    print(f"  EPG:     {DEFAULT_EPG_CACHE_DIR}")
    print(f"  TMDB:    {DEFAULT_TMDB_CACHE_DIR}")
    print(f"  Images:  {DEFAULT_IMAGE_CACHE_DIR}")
    print(f"  Log:     {report_log_path}")
    print(f"  History: {history_path}")

    logger.info(
        "Stats: %d bookmarked feed(s) sized, %d unknown, EPG cache dir %s, TMDB cache dir %s, "
        "image cache dir %s, log file %s, history file %s",
        len(sized_feeds),
        len(unknown_feeds),
        _format_cache_bytes(epg_dir_total),
        _format_cache_bytes(tmdb_size),
        _format_cache_bytes(image_size),
        _format_cache_bytes(log_size),
        _format_cache_bytes(history_size),
    )
    return 0


def run_hard_reset_command(argv: list[str]) -> int:
    """Handle `tvdinner hard-reset`: delete every file/directory tvdinner
    itself writes -- bookmarks, favorites, EPG shifts, a stored default
    TMDB token, schedule, playback positions, watch history, update-check
    state, the EPG/TMDB/image caches, and the log file -- so the next
    launch starts exactly as it would on a freshly installed system.
    Deliberately never touches --record-dir: a recording is real media content the
    user made, not disposable app state, and a "reset tvdinner" action
    has no business deleting that. Prompts for confirmation unless
    -y/--yes is given, listing every path first so nothing is a
    surprise."""
    parser = argparse.ArgumentParser(
        prog="tvdinner hard-reset",
        description="Delete all bookmarks, caches, and other data tvdinner has stored, reverting it to a "
        "freshly-installed state. Never touches recordings (--record-dir) -- those are your media, not app state.",
    )
    _add_config_path_args(parser)
    parser.add_argument(
        "--schedule-file",
        metavar="PATH",
        help=f"JSON file storing EPG-scheduled recordings (default: {DEFAULT_SCHEDULE_PATH})",
    )
    parser.add_argument(
        "--playback-positions-file",
        metavar="PATH",
        help=f"JSON file remembering playback positions (default: {DEFAULT_PLAYBACK_POSITIONS_PATH})",
    )
    parser.add_argument(
        "--history-file",
        metavar="PATH",
        help=f"JSONL file logging watch history (default: {DEFAULT_HISTORY_PATH})",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Don't prompt for confirmation before deleting")
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log startup/shutdown, user actions, and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--no-log", action="store_true", help="Disable file logging entirely")
    args = parser.parse_args(argv)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    logger.info("Starting tvdinner %s hard-reset", __version__)

    config_paths = _config_paths(args)
    files: list[tuple[str, Path]] = [
        ("Bookmarks", config_paths["bookmarks.json"]),
        ("Favorites", config_paths["favorites.json"]),
        ("EPG shifts", config_paths["epg_shifts.json"]),
        ("Stored default TMDB token", config_paths["tmdb_token.json"]),
        (
            "Scheduled recordings",
            Path(args.schedule_file) if args.schedule_file else DEFAULT_SCHEDULE_PATH,
        ),
        (
            "Playback positions",
            Path(args.playback_positions_file) if args.playback_positions_file else DEFAULT_PLAYBACK_POSITIONS_PATH,
        ),
        ("Watch history", Path(args.history_file) if args.history_file else DEFAULT_HISTORY_PATH),
        ("Update-check state", DEFAULT_UPDATE_CHECK_PATH),
    ]
    dirs: list[tuple[str, Path]] = [
        ("EPG cache (also holds the online channel/logo database)", DEFAULT_EPG_CACHE_DIR),
        ("TMDB cache", DEFAULT_TMDB_CACHE_DIR),
        ("Image cache (channel logos & poster art)", DEFAULT_IMAGE_CACHE_DIR),
    ]
    # The log file is deliberately handled separately from `files` above,
    # and removed last -- it's the one path this same process has open
    # for writing throughout the command (via configure_logging just
    # above), so unlinking it while still-open behaves differently across
    # platforms (see below).
    log_file_to_remove = log_path

    if not args.yes:
        print("This will permanently delete:")
        for label, path in files:
            print(f"  {label}: {path}")
        for label, path in dirs:
            print(f"  {label}: {path}")
        if log_file_to_remove is not None:
            print(f"  Log file: {log_file_to_remove}")
        print(f"\nRecordings ({DEFAULT_RECORDINGS_DIR} by default) are never touched.")
        answer = input("\nContinue? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Hard reset cancelled.")
            logger.info("Hard reset cancelled by user")
            return 0

    removed: list[str] = []
    for label, path in files:
        try:
            path.unlink()
            removed.append(label)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Warning: could not remove {label} ({path}): {exc}", file=sys.stderr)
            logger.warning("Could not remove %s (%s): %s", label, path, exc)

    for label, path in dirs:
        if not path.is_dir():
            continue
        try:
            shutil.rmtree(path)
            removed.append(label)
        except OSError as exc:
            print(f"Warning: could not remove {label} ({path}): {exc}", file=sys.stderr)
            logger.warning("Could not remove %s (%s): %s", label, path, exc)

    logger.info("Hard reset complete: removed %s", removed)

    if log_file_to_remove is not None:
        # Closed (see log.close_logging) before removal -- deleting a
        # file this process still has open for writing works on Linux
        # (the directory entry goes away immediately; the process keeps
        # appending to the now-unlinked inode until it exits or closes
        # the handle) but reliably fails on Windows (the file is locked
        # while open), so this is the one path actually closed rather
        # than just left to that platform difference. Nothing can be
        # logged to it after this, hence doing it last.
        close_logging(log_file_to_remove)
        try:
            log_file_to_remove.unlink()
            removed.append("Log file")
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Warning: could not remove log file ({log_file_to_remove}): {exc}", file=sys.stderr)
        # Rotated backups (tvdinner.log.1, ...) live alongside the live file
        # but aren't tracked by any open handler, so they need their own
        # cleanup pass rather than being covered by close_logging above.
        for backup in log_file_to_remove.parent.glob(f"{log_file_to_remove.name}.*"):
            try:
                backup.unlink()
                removed.append("Log file backup")
            except OSError as exc:
                print(f"Warning: could not remove log file backup ({backup}): {exc}", file=sys.stderr)

    if not removed:
        print("Nothing to remove -- tvdinner already has no stored data.")
    else:
        print(f"Removed {len(removed)} item(s):")
        for label in removed:
            print(f"  {label}")
    return 0


def run_store_tmdb_command(argv: list[str]) -> int:
    """Handle `tvdinner store-tmdb TOKEN`: save a TMDB API token as the
    default used whenever --tmdb-api-token isn't given directly (see
    main()'s tmdb_api_token resolution -- an explicit --tmdb-api-token,
    including one carried by a bookmark's own saved token, always
    overrides this)."""
    parser = argparse.ArgumentParser(
        prog="tvdinner store-tmdb",
        description="Save a TMDB v4 read-access Bearer token as the default used when --tmdb-api-token isn't given.",
    )
    parser.add_argument("token", metavar="TOKEN", help="TMDB v4 read-access Bearer token")
    parser.add_argument(
        "--tmdb-token-file", metavar="PATH", help=f"Where to store it (default: {DEFAULT_TMDB_TOKEN_PATH})"
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

    path = Path(args.tmdb_token_file) if args.tmdb_token_file else DEFAULT_TMDB_TOKEN_PATH
    try:
        save_tmdb_token(path, args.token)
    except OSError as exc:
        print(f"Could not save TMDB token to {path}: {exc}", file=sys.stderr)
        logger.error("Could not save TMDB token to %s: %s", path, exc)
        return 1
    print(f"TMDB token saved to {path}.")
    # Never logs the token itself -- same redact-before-logging norm as
    # every other credential in this codebase (see run_bookmarks_command's
    # own comment on the same point).
    logger.info("TMDB token saved to %s", path)
    return 0


def run_clear_tmdb_command(argv: list[str]) -> int:
    """Handle `tvdinner clear-tmdb`: remove the stored default TMDB API
    token, if any."""
    parser = argparse.ArgumentParser(
        prog="tvdinner clear-tmdb",
        description="Remove the stored default TMDB API token, if any.",
    )
    parser.add_argument(
        "--tmdb-token-file", metavar="PATH", help=f"Where it's stored (default: {DEFAULT_TMDB_TOKEN_PATH})"
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

    path = Path(args.tmdb_token_file) if args.tmdb_token_file else DEFAULT_TMDB_TOKEN_PATH
    if clear_tmdb_token(path):
        print(f"Removed stored TMDB token ({path}).")
        logger.info("Removed stored TMDB token: %s", path)
    else:
        print("No stored TMDB token to remove.")
        logger.info("No stored TMDB token to remove (%s)", path)
    return 0


def run_gdrive_login_command(argv: list[str]) -> int:
    """Handle `tvdinner gdrive-login`: run the interactive Google OAuth
    consent flow and store the resulting credentials for later use by
    `tvdinner backup --gdrive`/`tvdinner restore --gdrive`. Uses
    tvdinner's own bundled OAuth client by default (see gdrive.py's
    BUNDLED_CLIENT_ID for why that's safe to ship); --client-id/
    --client-secret opt into a different "Desktop app" OAuth client
    instead, e.g. to avoid sharing the bundled one's request quota."""
    parser = argparse.ArgumentParser(
        prog="tvdinner gdrive-login",
        description="Sign in to Google Drive for 'tvdinner backup --gdrive'/'tvdinner restore --gdrive'.",
    )
    parser.add_argument(
        "--client-id", metavar="ID", help="Use this OAuth client ID instead of tvdinner's own bundled one"
    )
    parser.add_argument(
        "--client-secret", metavar="SECRET", help="Use this OAuth client secret instead of tvdinner's own bundled one"
    )
    parser.add_argument(
        "--gdrive-token-file",
        metavar="PATH",
        help=f"Where to store the sign-in (default: {DEFAULT_GDRIVE_TOKEN_PATH})",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Don't try to open a browser automatically; just print the URL"
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

    token_path = Path(args.gdrive_token_file) if args.gdrive_token_file else DEFAULT_GDRIVE_TOKEN_PATH

    client_id = args.client_id
    client_secret = args.client_secret
    if not client_id or not client_secret:
        # No explicit override -- reuse whichever client a previous login
        # already stored (so re-logging in after a token expiry/revoke
        # doesn't silently switch clients), falling back to tvdinner's
        # own bundled one for a first-time login.
        existing, _warnings = load_gdrive_credentials(token_path)
        client_id = client_id or (existing["client_id"] if existing else None) or BUNDLED_CLIENT_ID
        client_secret = client_secret or (existing["client_secret"] if existing else None) or BUNDLED_CLIENT_SECRET

    try:
        credentials = gdrive_login(client_id, client_secret, open_browser=not args.no_browser)
    except GdriveError as exc:
        print(f"Google Drive sign-in failed: {exc}", file=sys.stderr)
        logger.error("Google Drive sign-in failed: %s", exc)
        return 1

    try:
        save_gdrive_credentials(token_path, **credentials)
    except OSError as exc:
        print(f"Could not save Google Drive credentials to {token_path}: {exc}", file=sys.stderr)
        logger.error("Could not save Google Drive credentials to %s: %s", token_path, exc)
        return 1
    print(f"Signed in to Google Drive. Credentials saved to {token_path}.")
    # Never logs the client secret or tokens -- same redact-before-logging
    # norm as every other credential in this codebase.
    logger.info("Signed in to Google Drive; credentials saved to %s", token_path)
    return 0


def run_gdrive_logout_command(argv: list[str]) -> int:
    """Handle `tvdinner gdrive-logout`: remove the stored Google Drive
    sign-in, if any. Does not revoke the OAuth grant on Google's side --
    see https://myaccount.google.com/permissions to do that."""
    parser = argparse.ArgumentParser(
        prog="tvdinner gdrive-logout",
        description="Forget the stored Google Drive sign-in, if any.",
    )
    parser.add_argument(
        "--gdrive-token-file", metavar="PATH", help=f"Where it's stored (default: {DEFAULT_GDRIVE_TOKEN_PATH})"
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

    path = Path(args.gdrive_token_file) if args.gdrive_token_file else DEFAULT_GDRIVE_TOKEN_PATH
    if clear_gdrive_credentials(path):
        print(f"Removed stored Google Drive sign-in ({path}).")
        logger.info("Removed stored Google Drive sign-in: %s", path)
    else:
        print("No stored Google Drive sign-in to remove.")
        logger.info("No stored Google Drive sign-in to remove (%s)", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    if not raw_argv:
        # No subcommand and no `url` positional at all -- rather than
        # argparse's usual "the following arguments are required: url"
        # error, a bare `tvdinner` is common enough (the natural thing to
        # type when you just want to pick from what's already saved) that
        # it's worth treating the same as `tvdinner bookmarks` instead.
        return run_bookmarks_command([])
    if raw_argv[:1] == ["bookmarks"]:
        return run_bookmarks_command(raw_argv[1:])
    if raw_argv[:1] == ["backup"]:
        return run_backup_command(raw_argv[1:])
    if raw_argv[:1] == ["restore"]:
        return run_restore_command(raw_argv[1:])
    if raw_argv[:1] == ["stats"]:
        return run_stats_command(raw_argv[1:])
    if raw_argv[:1] == ["hard-reset"]:
        return run_hard_reset_command(raw_argv[1:])
    if raw_argv[:1] == ["store-tmdb"]:
        return run_store_tmdb_command(raw_argv[1:])
    if raw_argv[:1] == ["clear-tmdb"]:
        return run_clear_tmdb_command(raw_argv[1:])
    if raw_argv[:1] == ["gdrive-login"]:
        return run_gdrive_login_command(raw_argv[1:])
    if raw_argv[:1] == ["gdrive-logout"]:
        return run_gdrive_logout_command(raw_argv[1:])

    args = build_parser().parse_args(argv)
    # A copy-pasted example URL (this project's own docs show them shell-
    # quoted, e.g. tvdinner 'hdhomerun://192.168.1.50') can end up with
    # literal quote characters baked in if pasted somewhere that isn't a
    # shell -- a saved bookmark, or a launcher/script that doesn't do
    # shell-style quote removal. Strip a single matching pair here so
    # that mistake doesn't silently break scheme detection.
    args.url = strip_wrapping_quotes(args.url)
    if args.epg:
        args.epg = strip_wrapping_quotes(args.epg)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    logger.info(
        "Starting tvdinner %s (playlist=%s, epg=%s, channel=%s)",
        __version__,
        redact_plex_url(redact_stalker_url(redact_xtream_url(args.url))),
        args.epg,
        args.channel,
    )

    def update_checker() -> UpdateInfo | None:
        # Defined once here, up front, so it's available uniformly to
        # every source branch below (Xtream/Stalker/HDHomeRun/Plex/M3U/
        # direct-stream/local-file) -- being up to date is orthogonal to
        # which kind of source was given.
        if args.no_update_check:
            return None
        state, warnings = load_update_check_state(DEFAULT_UPDATE_CHECK_PATH)
        for warning in warnings:
            logger.warning(warning)
        now = datetime.now(timezone.utc)
        if not should_check_now(state, now):
            return None
        info, error = check_for_update(__version__)
        # last_checked is updated regardless of the fetch's own
        # success/failure, so a transient network error backs off for a
        # full day rather than retrying on every single launch.
        state.last_checked = now
        try:
            save_update_check_state(DEFAULT_UPDATE_CHECK_PATH, state)
        except OSError as exc:
            logger.warning("Could not save update-check state to %s: %s", DEFAULT_UPDATE_CHECK_PATH, exc)
        if error:
            logger.warning("Could not check for updates: %s", error)
            return None
        if info is None or info.version == state.skipped_version:
            return None
        return info

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

    history_path = None if args.no_history else (Path(args.history_file) if args.history_file else DEFAULT_HISTORY_PATH)

    # An explicit --tmdb-api-token always wins -- including one carried by
    # a bookmark's own saved token, which arrives here the same way (see
    # run_bookmarks_command, which funnels it through as this same flag
    # when re-entering main()) -- falling back to whatever's stored via
    # `tvdinner store-tmdb`, if anything. Every other tmdb_api_token use
    # below reads this resolved value, never args.tmdb_api_token directly.
    tmdb_token_path = Path(args.tmdb_token_file) if args.tmdb_token_file else DEFAULT_TMDB_TOKEN_PATH
    stored_tmdb_token, tmdb_token_warnings = load_tmdb_token(tmdb_token_path)
    for warning in tmdb_token_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)
    tmdb_api_token = args.tmdb_api_token or stored_tmdb_token

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

    vod_items: list[VodItem] = []

    if is_xtream_url(args.url):
        creds = parse_xtream_url(args.url)
        if creds is None:
            print(
                "Invalid xtream:// URL: expected xtream://username:password@host:port", file=sys.stderr
            )
            logger.error("Invalid xtream:// URL: %s", redact_xtream_url(args.url))
            return 1
        playlist, xtream_error = load_xtream_playlist(creds)
        if playlist is None:
            print(f"Xtream error: {xtream_error}", file=sys.stderr)
            logger.error("Xtream error: %s", xtream_error)
            return 1
        vod_items, vod_error = load_xtream_vod(creds)
        if vod_error:
            logger.warning("Could not load Xtream VOD library (continuing without it): %s", vod_error)
    elif is_stalker_url(args.url):
        stalker_creds = parse_stalker_url(args.url)
        if stalker_creds is None:
            print(
                "Invalid stalker:// URL: expected stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF",
                file=sys.stderr,
            )
            logger.error("Invalid stalker:// URL: %s", redact_stalker_url(args.url))
            return 1
        playlist, stalker_error = load_stalker_playlist(stalker_creds)
        if playlist is None:
            print(f"Stalker error: {stalker_error}", file=sys.stderr)
            logger.error("Stalker error: %s", stalker_error)
            return 1
        vod_items, vod_error = load_stalker_vod(stalker_creds)
        if vod_error:
            logger.warning("Could not load Stalker VOD library (continuing without it): %s", vod_error)
    elif is_hdhomerun_url(args.url):
        hdhomerun_target = parse_hdhomerun_url(args.url)
        if hdhomerun_target is None:
            print("Invalid hdhomerun:// URL: expected hdhomerun://host[:port]", file=sys.stderr)
            logger.error("Invalid hdhomerun:// URL: %s", args.url)
            return 1
        playlist, hdhomerun_error = load_hdhomerun_playlist(hdhomerun_target)
        if playlist is None:
            print(f"HDHomeRun error: {hdhomerun_error}", file=sys.stderr)
            logger.error("HDHomeRun error: %s", hdhomerun_error)
            return 1
    elif is_plex_url(args.url):
        # Unlike the other three sources, Plex has no live-channel/EPG
        # concept at all -- it's 100% on-demand -- so this returns early,
        # bypassing the "if not playlist.channels:" gate below entirely
        # (playlist is never assigned in this branch). list_plex_libraries
        # doubles as both the connectivity/auth check and the root-frame
        # data source, so play_stream's Plex browser needs zero further
        # network round-trips to show its first screen.
        plex_creds = parse_plex_url(args.url)
        if plex_creds is None:
            print("Invalid plex:// URL: expected plex://host:port?X-Plex-Token=...", file=sys.stderr)
            logger.error("Invalid plex:// URL: %s", redact_plex_url(args.url))
            return 1
        print("Connecting to Plex server...", file=sys.stderr)
        plex_root_nodes, plex_error = list_plex_libraries(plex_creds)
        if plex_error:
            print(f"Plex error: {plex_error}", file=sys.stderr)
            logger.error("Plex error: %s", plex_error)
            return 1
        if not plex_root_nodes:
            print("No movie or TV libraries found on this Plex server.", file=sys.stderr)
            logger.error("No movie or TV libraries found on Plex server %s", plex_creds.base_url)
            return 1
        logger.info("Connected to Plex server at %s (%d libraries)", plex_creds.base_url, len(plex_root_nodes))
        # Keyed by plex_creds.base_url, never the raw token-bearing
        # args.url -- same defense-in-depth reasoning as passing
        # url=plex_creds.base_url to play_stream below, just applied to
        # the favorites file's feed key instead of a log line.
        plex_favorites, plex_favorites_warnings = load_favorites(favorites_path, plex_creds.base_url)
        for warning in plex_favorites_warnings:
            print(f"Warning: {warning}", file=sys.stderr)
            logger.warning(warning)
        # A stable id across runs (see load_plex_client_id's own docstring)
        # -- loaded unconditionally, even with --no-plex-activity, since
        # it's cheap and harmless to have ready either way.
        plex_client_id = load_plex_client_id()
        # url=plex_creds.base_url (never the raw token-bearing args.url) --
        # defense in depth so the token can never leak into the
        # "Starting playback: %s (%s)" log line even if the play()-skip
        # guard for plex_creds is ever accidentally removed later.
        return play_stream(
            plex_creds.base_url,
            title="Plex Library",
            plex_creds=plex_creds,
            plex_root_nodes=plex_root_nodes,
            plex_client_id=plex_client_id,
            plex_activity_reporting=not args.no_plex_activity,
            record_dir=record_dir,
            live_buffer_minutes=args.live_buffer_minutes,
            playback_positions=playback_positions,
            playback_positions_path=playback_positions_path,
            update_checker=update_checker,
            full_screen=not args.disable_full_screen,
            glsl_shader=args.glsl_shader,
            interpolation=args.interpolation,
            playlist_source=plex_creds.base_url,
            history_path=history_path,
            favorites=plex_favorites,
            favorites_path=favorites_path,
            favorites_feed=plex_creds.base_url,
            tmdb_api_token=tmdb_api_token,
        )
    elif Path(args.url).expanduser().is_file() and not looks_like_m3u_path(Path(args.url).expanduser()):
        # A local file that isn't itself an M3U playlist -- a movie file
        # to play directly, no playlist/EPG/channel involved. Checked via
        # a cheap content sniff (looks_like_m3u_path), not just extension,
        # so a genuine local M3U playlist (the "or local file path" case
        # documented on `url` above) still falls through to the playlist
        # branch below as before. It carries no provider metadata of its
        # own, so its identity is guessed from the filename (see
        # localfile.guess_movie_title_year, and --title/--year to
        # override a bad guess) and, if --tmdb-api-token is given, looked
        # up on TMDB in the background so the 'i' overlay gets the same
        # poster/synopsis/rating any other VOD source shows (see
        # vod.VodItem).
        path = Path(args.url).expanduser()
        guessed_title, guessed_year = guess_movie_title_year(path)
        title = args.title or guessed_title
        year = args.year or guessed_year
        logger.info("Guessed movie identity for %s: %r (%s)", path.name, title, year or "unknown year")

        vod_metadata_loader = None
        if tmdb_api_token:
            # An explicit --title override is trusted outright, same as
            # the YouTube branch below; the auto-guessed case tries a
            # couple of candidate search strings in turn (see
            # movietitle.title_search_candidates), since a filename can
            # chain the same kind of cast/tagline/videoID noise onto the
            # real title a YouTube video's own title can (confirmed live:
            # a yt-dlp download of an archive-channel upload, filename
            # "1940 - His Girl Friday - Cary Grant and Rosalind Russell -
            # Ex-lovers become headline hunters [wEx-z1TYPKU].webm", only
            # matches TMDB on its first segment, "His Girl Friday").
            lookup_candidates = [title] if args.title else title_search_candidates(title)

            def vod_metadata_loader() -> VodItem | None:
                metadata = None
                for candidate in lookup_candidates:
                    metadata = fetch_movie_metadata_cached(candidate, year, tmdb_api_token)
                    if metadata is not None:
                        break
                if metadata is None:
                    return None
                return VodItem(
                    title=metadata.title,
                    url=str(path),
                    year=metadata.year or year,
                    rating=metadata.rating,
                    rating_is_tmdb=metadata.rating is not None,
                    description=metadata.overview,
                    poster_url=metadata.poster_url,
                    director=metadata.director,
                    backdrop_url=metadata.backdrop_url,
                    logo_url=metadata.logo_url,
                )

        return play_stream(
            str(path),
            title=title,
            initial_vod_item=VodItem(title=title, url=str(path), year=year),
            vod_metadata_loader=vod_metadata_loader,
            record_dir=record_dir,
            playback_positions=playback_positions,
            playback_positions_path=playback_positions_path,
            update_checker=update_checker,
            full_screen=not args.disable_full_screen,
            glsl_shader=args.glsl_shader,
            interpolation=args.interpolation,
            history_path=history_path,
        )
    elif is_youtube_url(args.url):
        # mpv already plays a plain YouTube URL directly via its built-in
        # yt-dlp hook -- no playlist/EPG/channel involved, so like the
        # local-file branch above this is a VOD session with nothing to
        # browse, reusing the exact same play_stream wiring
        # (initial_vod_item/vod_metadata_loader). Unlike a local file,
        # its title/thumbnail/uploader come from YouTube's own public
        # oEmbed endpoint (no API key needed, always tried) rather than a
        # filename guess; --tmdb-api-token additionally tries a TMDB
        # lookup on that title, trying a few candidate search strings in
        # turn (movietitle.title_search_candidates), since a real video
        # title often chains cast/tagline/genre text onto the actual
        # movie name that would otherwise sink the search entirely -- for
        # a richer poster/synopsis/rating/director on the rare video
        # that's a real movie. Not gated on the title carrying a year
        # (confirmed live: a real official-studio upload titled
        # "McLintock! | FULL MOVIE | John Wayne, Maureen O'Hara | Western
        # Rancher Cowboy Comedy" has none, yet title_search_candidates'
        # own separator-splitting already isolates "McLintock!" as its
        # first, presumably-just-the-movie-name candidate -- which is
        # exactly what finds it on TMDB) -- matches the local-file
        # branch above, which never gated on year presence either.
        # title= is deliberately left unset below (unlike the local-file
        # branch) so mpv's own yt-dlp hook keeps setting the window title
        # from the resolved video's real metadata, same as it already did
        # before this branch existed.
        youtube_url = args.url

        def vod_metadata_loader() -> VodItem | None:
            info = fetch_youtube_oembed(youtube_url)
            if info is None:
                return None
            item = VodItem(
                title=info.title,
                url=youtube_url,
                poster_url=info.thumbnail_url,
                description=f"YouTube · {info.author_name}" if info.author_name else None,
            )
            if tmdb_api_token:
                if args.title or args.year:
                    # An explicit override is the user asserting outright
                    # that this is a movie, so unlike the auto-guessed
                    # case below, it always triggers a lookup even
                    # without a detected year, and is trusted outright
                    # rather than run through title_search_candidates'
                    # own guessing.
                    lookup_candidates, lookup_year = [args.title or info.title], args.year
                else:
                    lookup_title, lookup_year = guess_title_year(info.title)
                    lookup_candidates = title_search_candidates(lookup_title)
                metadata = None
                for candidate in lookup_candidates:
                    metadata = fetch_movie_metadata_cached(candidate, lookup_year, tmdb_api_token)
                    if metadata is not None:
                        break
                if metadata is not None:
                    item = VodItem(
                        title=metadata.title,
                        url=youtube_url,
                        year=metadata.year,
                        rating=metadata.rating,
                        rating_is_tmdb=metadata.rating is not None,
                        description=metadata.overview or item.description,
                        poster_url=metadata.poster_url or item.poster_url,
                        director=metadata.director,
                        backdrop_url=metadata.backdrop_url,
                        logo_url=metadata.logo_url,
                    )
            return item

        return play_stream(
            args.url,
            initial_vod_item=VodItem(title="YouTube", url=args.url),
            vod_metadata_loader=vod_metadata_loader,
            record_dir=record_dir,
            playback_positions=playback_positions,
            playback_positions_path=playback_positions_path,
            update_checker=update_checker,
            full_screen=not args.disable_full_screen,
            glsl_shader=args.glsl_shader,
            interpolation=args.interpolation,
            history_path=history_path,
        )
    else:
        # A real playlist can take a while to fetch (some feeds are
        # served through a slow redirect chain -- confirmed live against
        # m3u4u.com's redirect to Dropbox taking several seconds even
        # after fixing _fetch_text's earlier double-request bug), so this
        # is worth a visible sign of life rather than the terminal
        # looking hung the way a genuinely stuck request would.
        print("Loading playlist...", file=sys.stderr)
        playlist = load_playlist(args.url)

        if playlist is None:
            # Doesn't look like an M3U playlist -- treat it as a direct stream URL.
            logger.info(
                "'%s' doesn't look like an M3U playlist; treating it as a direct stream URL",
                redact_plex_url(redact_stalker_url(redact_xtream_url(args.url))),
            )
            return play_stream(
                args.url,
                record_dir=record_dir,
                live_buffer_minutes=args.live_buffer_minutes,
                update_checker=update_checker,
                full_screen=not args.disable_full_screen,
                glsl_shader=args.glsl_shader,
                interpolation=args.interpolation,
                history_path=history_path,
            )

        vod_items, playlist.channels = split_m3u_vod_items(playlist, set(args.vod_group or []))

    logger.info("Loaded playlist: %d channels, %d VOD items", len(playlist.channels), len(vod_items))

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
            playlist,
            override=args.epg,
            cache_dir=epg_cache_dir,
            max_age=epg_max_age,
            on_progress=_make_epg_progress_reporter("EPG data"),
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
    # background once mpv is already under way. `on_message`, if given by
    # the caller (play_stream, once the player window exists), mirrors
    # the same throttled progress text onto the player's own on-screen
    # OSD, not just the terminal.
    def epg_loader(on_message: Callable[[str], None] | None = None) -> Epg | None:
        return load_epg_for_playlist(
            playlist,
            override=args.epg,
            cache_dir=epg_cache_dir,
            max_age=epg_max_age,
            on_progress=_make_epg_progress_reporter("EPG data", on_message=on_message),
        )

    # Shares the EPG cache's directory/max-age (see --epg-cache-hours/
    # --no-epg-cache/--refresh-epg-cache above) -- one set of cache
    # settings to reason about, rather than a second one just for this.
    online_logos_loader = (
        None
        if args.no_online_logos
        else lambda: load_online_logo_index(cache_dir=epg_cache_dir, max_age=epg_max_age)
    )

    if args.channel:
        channel = select_channel(playlist.channels, args.channel)
        if channel is None:
            print(f"Channel not found: {args.channel}", file=sys.stderr)
            logger.error("Channel not found: %s", args.channel)
            return 1
    else:
        channel = hd_first(playlist.channels)[0]

    return play_stream(
        channel.url,
        title=channel.name,
        channel=channel,
        channels=playlist.channels,
        vod_items=vod_items,
        epg_loader=epg_loader,
        online_logos_loader=online_logos_loader,
        tmdb_api_token=tmdb_api_token,
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
        update_checker=update_checker,
        full_screen=not args.disable_full_screen,
        glsl_shader=args.glsl_shader,
        interpolation=args.interpolation,
        playlist_source=redact_plex_url(redact_stalker_url(redact_xtream_url(args.url))),
        history_path=history_path,
    )


if __name__ == "__main__":
    sys.exit(main())
