"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from tvdinner import __version__
from tvdinner.backup import create_backup, restore_backup
from tvdinner.bookmarks import (
    DEFAULT_BOOKMARKS_PATH,
    Bookmark,
    BookmarkError,
    bookmark_to_dict,
    find_bookmark,
    load_bookmarks,
    remove_bookmark,
    save_bookmarks,
    upsert_bookmark,
)
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
from tvdinner.favorites import DEFAULT_FAVORITES_PATH, load_favorites, remove_favorites_feed, save_favorites
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
    cached_image,
    chapter_thumbnail_url,
    fetch_image,
    forget_failed_fetch,
    guide_eligible_channels,
    guide_reference_time,
    help_tab_count,
    jump_to_letter_index,
    plex_row_thumb_url,
    prefetch_channel_logos,
    prefetch_images,
    recording_thumbnail_url,
    render_about_overlay,
    render_cast_picker,
    render_chapter_preview_overlay,
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
    render_series_browser,
    render_skip_marker_overlay,
    render_up_next_backdrop,
    render_up_next_overlay,
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
    visible_series_nodes,
)
from tvdinner.player import (
    DEFAULT_LIVE_BUFFER_MINUTES,
    DEFAULT_RECORDINGS_DIR,
    Player,
    PlexThemePlayer,
    RecordingFile,
    StreamInfo,
    list_recordings,
    live_buffer_mpv_options,
)
from tvdinner.playback_positions import (
    DEFAULT_PLAYBACK_POSITIONS_PATH,
    load_playback_positions,
    playback_position_timestamps_path_for,
    save_playback_positions,
)
from tvdinner.plex import (
    PlexCreds,
    PlexNode,
    find_next_episode,
    is_plex_url,
    list_plex_libraries,
    list_plex_node_children,
    load_plex_client_id,
    mark_plex_unwatched,
    mark_plex_watched,
    parse_plex_url,
    plex_theme_url,
    redact_plex_url,
    report_plex_timeline,
    resolve_plex_playable,
    search_plex,
    search_plex_by_year,
    show_tmdb_id as plex_show_tmdb_id,
)
from tvdinner.redact import redact_resource_url, stable_credential_key
from tvdinner.schedule import DEFAULT_SCHEDULE_PATH, ScheduledRecording, load_schedule, save_schedule
from tvdinner.series import SeriesNode
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
    fetch_movie_logo_cached,
    fetch_movie_metadata_cached,
    fetch_tv_logo_cached,
    is_movie_category,
    movie_id_for,
    prefetch_backdrop,
    prefetch_director,
    prefetch_logo,
    prefetch_movie_id,
    prefetch_ratings,
    prefetch_release_year,
)
from tvdinner.tmdb_config import DEFAULT_TMDB_TOKEN_PATH, clear_tmdb_token, load_tmdb_token, save_tmdb_token
from tvdinner.tvtimes import (
    TvtimesFeed,
    fetch_tvtimes_favourites,
    fetch_tvtimes_watchlist,
    post_watch_events,
    is_tvtimes_url,
    parse_tvtimes_url,
    tvtimes_epg_url,
    tvtimes_playlist_url,
    watch_events_payload,
    watchlist_schedule_updates,
)
from tvdinner.update_check import (
    DEFAULT_UPDATE_CHECK_PATH,
    UpdateInfo,
    check_for_update,
    load_update_check_state,
    save_update_check_state,
    should_check_now,
)
from tvdinner.vod import VodItem, VodMarker, sort_vod_items, split_m3u_vod_items
from tvdinner.xtream import (
    XtreamCreds,
    is_xtream_url,
    list_xtream_series_children,
    load_xtream_playlist,
    load_xtream_vod,
    parse_xtream_url,
    redact_xtream_url,
    resolve_xtream_series_episode,
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
_SERIES_OVERLAY_ID = 21
_SKIP_MARKER_OVERLAY_ID = 17
# mpv composites overlays in ascending id order (higher id on top), so the
# full-screen "Up Next" backdrop MUST have a lower id than the countdown card
# it sits behind -- otherwise the opaque backdrop hides the card entirely.
_UP_NEXT_BACKDROP_OVERLAY_ID = 18
_UP_NEXT_OVERLAY_ID = 19
_CHAPTER_PREVIEW_OVERLAY_ID = 20
_GUIDE_TIME_STEP = timedelta(minutes=30)
_SHIFT_NUDGE_STEP = timedelta(minutes=1)
_GUIDE_MAX_ROWS = 8  # kept in sync with render_and_show_guide's max_rows so a page = a full screen
_RECORDINGS_MAX_ROWS = 8  # kept in sync with render_and_show_recordings's max_rows, like _GUIDE_MAX_ROWS
_SCHEDULE_MAX_ROWS = 8  # kept in sync with render_and_show_schedule's max_rows, like _GUIDE_MAX_ROWS
_VOD_MAX_ROWS = 8  # kept in sync with render_and_show_vod's max_rows, like _GUIDE_MAX_ROWS
_SERIES_MAX_ROWS = 8  # kept in sync with render_and_show_series's max_rows, like _GUIDE_MAX_ROWS
_HISTORY_MAX_ROWS = 6  # kept in sync with render_and_show_history's max_rows -- shorter than the others, since each row is taller (a thumbnail plus two lines of text)
_MISSED_SCHEDULE_HISTORY_LIMIT = 10  # capped so a long session's conflicts don't grow the 'u' view unbounded
_RESUME_MIN_SECONDS = 10.0  # don't bother resuming a recording barely started
_RESUME_END_MARGIN_SECONDS = 15.0  # this close to the end counts as "fully watched" -- start over, don't resume
_CHAPTER_SKIP_BACK_THRESHOLD_SECONDS = 5.0  # DOWN this far into the current chapter jumps to its own start; earlier than that, to the previous chapter's
_CHAPTER_PREVIEW_COMMIT_SECONDS = 6.0  # idle timeout before the chapter-scrub preview auto-commits its seek -- long
# enough to actually see the thumbnail arrive most of the time (confirmed live: a local-frame-grab-fallback fetch
# against a real, actively-streaming Plex item took 3.6-7.2s, so anything shorter meant the preview auto-committed,
# and its overlay was cleared, before the thumbnail could ever actually be seen)
_CHAPTER_THUMB_SEEK_OFFSET_SECONDS = 2.0  # local-frame-grab fallback only (see _chapter_preview_thumb_url): a
# chapter's own start_seconds routinely lands exactly on a fade-to-black transition frame -- confirmed live
# (extrema (0, 0)) against a real TV-rip's commercial-break-aligned chapter markers -- so grab a couple of seconds
# past the boundary instead, which reliably lands on real content (same chapter, +2s: extrema (0, 255))
_PLAYBACK_POSITION_AUTOSAVE_SECONDS = 10.0  # periodic, not just on switch/quit -- mpv's core is already gone by the
# time the 'finally' block runs after the user quits via mpv's own default 'q', so that alone can't be relied on
_SKIP_MARKER_POLL_SECONDS = 1.0  # frequent enough that the "Skip Intro"/"Skip Credits" prompt appears close to when the window actually starts
_UP_NEXT_TICK_SECONDS = 1.0  # cadence of the "Up Next" countdown's own re-render/reschedule
_SLEEP_TIMER_PRESETS_MINUTES = (0, 15, 30, 60, 90)  # 0 = Off; cycled through by the 'e' key, same idiom as _ASPECT_RATIOS
_PLEX_THEME_DEBOUNCE_SECONDS = 1.0  # dwell time on a show before its theme starts -- avoids firing on every scroll step
_PLEX_THEME_VOLUME = 55.0  # background ambience while browsing, not full blast
_PLEX_THEME_FADE_STEPS = 6
_PLEX_THEME_FADE_INTERVAL_SECONDS = 0.05  # ~300ms total fade -- short enough not to overlap audibly with what plays next
# Keys with no meaning outside the guide; suspended while typing a filter
# query too, since they have no character-input equivalent to shadow them.
_GUIDE_NAV_ONLY_KEYS = ("LEFT", "RIGHT", "UP", "DOWN", "PGUP", "PGDWN", "[", "]")
_FILTER_INPUT_CHARS = list("abcdefghijklmnopqrstuvwxyz0123456789")
# Letters carved out of the Plex browser's jump-nav (unlike the VOD
# browser, which shadows the full _FILTER_INPUT_CHARS set) -- these are
# the Plex-mode single-letter actions most worth reaching without
# backing out of a movie/show listing first: g (grid/list view -- the
# one place that view even applies), h (favorite), v (favorites-only),
# l (close -- redundant with ESC, but symmetrical with the others), y
# (year filter -- '/' search covers the same need). Jumping straight to
# a title starting with one of these is the trade-off; a couple of
# arrow presses (or '/' search) still gets there.
_PLEX_JUMP_NAV_CHARS = [c for c in _FILTER_INPUT_CHARS if c not in "ghvly"]
_YEAR_INPUT_CHARS = list("0123456789")
_YEAR_INPUT_MAX_DIGITS = 4
_DEFAULT_CANVAS_WIDTH = 1920
_DEFAULT_CANVAS_HEIGHT = 1080
_OSD_SIZE_WAIT_SECONDS = 2.0
_OSD_SIZE_POLL_INTERVAL = 0.05
_SCHEDULE_POLL_SECONDS = 15.0
# The tvtimes watchlist changes on human timescales (a reminder is set
# well before airtime), so this is far slower than the schedule tick --
# it's a network round-trip to another host, not a clock comparison.
_WATCHLIST_POLL_SECONDS = 900.0
# Watch state is reported by resending a trailing window of the history
# log rather than tracking what's already been sent -- tvtimes dedupes on
# (channel_id, started_at), so a restart or an outage self-heals with no
# local bookkeeping to fall out of step. The window only has to outlast a
# plausible outage.
_WATCH_REPORT_SECONDS = 900.0
_WATCH_REPORT_WINDOW = timedelta(days=7)
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
    # The kind of the container row this frame was drilled in from
    # ("library_movie" / "library_show" / "show" / "season" /
    # "continue_watching") -- None only for a synthetic root / search /
    # year-filter frame. render_and_show_plex uses it to tell the On Deck
    # listing apart (source_kind == "continue_watching"), where an episode
    # row shows its season poster rather than the episode still.
    source_kind: str | None = None


@dataclass
class _SeriesNavFrame:
    """One level of cli.py's Series browser nav stack (Xtream today; see
    tvdinner.series) -- mirrors _PlexNavFrame above exactly, just over
    SeriesNode instead of PlexNode. Pushed by drilling into a container
    node (a category, series, or season), popped by ESC. The browser
    closes once the last frame is popped."""

    breadcrumb: str
    nodes: list[SeriesNode]
    selected_index: int = 0


def _plex_title_logo_target(
    nav_stack: list[_PlexNavFrame], frame_nodes: Callable[[_PlexNavFrame], list[PlexNode]]
) -> PlexNode | None:
    """The movie/show whose name TMDB's title-logo search
    (render_and_show_plex) should use, and whose real rating_key
    cli.py's Plex theme-music feature should fetch a theme for, since a
    season or episode listing has no title/theme of its own. None if
    there isn't one at all (browsing a library/Continue-Watching list
    itself).

    `frame_nodes` must be cli.py's own plex_frame_nodes -- a frame's
    `nodes` is always the full, unfiltered listing, but `selected_index`
    indexes whatever's actually on screen, which is the
    favorites-only-filtered subset when that toggle is on. Indexing
    frame.nodes directly with it picks out a different, unrelated item
    whenever the filter is active -- confirmed live: favoriting "The
    Green Berets" and switching to favorites-only correctly showed its
    backdrop (render_and_show_plex already used the filtered list) but
    still showed the title logo of whatever the *unfiltered* list's
    same-index item happened to be.

    A movie/show selected directly is returned as-is. An episode
    selected in *any* listing -- a season's own episode list, Continue
    Watching's flat on-deck list, or a search/year-filter result list --
    uses its own grandparent_rating_key (Plex's grandparentRatingKey,
    read straight off the episode itself regardless of listing context
    -- see plex.py's _episode_node) to build a lightweight stand-in show
    node with that *real* rating_key, falling back to a synthetic node
    keyed by series_title (Plex's grandparentTitle) only on the rare
    item that's missing even that.

    This deliberately does *not* walk the nav stack outward looking for
    a real show ancestor frame the way an earlier version did (confirmed
    live: doing that for an episode picked up whatever unrelated show
    happened to be selected in whatever frame was sitting underneath a
    flat episode listing in the stack -- e.g. searching for and
    selecting an episode played the theme of a show browsed earlier in
    an entirely different part of the library, since that show's frame
    was still sitting under the search-results frame the search was
    opened from). A "season" listing (browsing a show's own seasons,
    nothing drilled into yet) has no such per-item ancestor field of its
    own, but is only ever reached by drilling into a real show first --
    unlike an episode, it can never turn up in an unrelated flat listing
    like search results -- so walking outward one level to that
    genuinely-real ancestor frame is still safe there."""
    if not nav_stack:
        return None
    frame = nav_stack[-1]
    nodes = frame_nodes(frame)
    if not (0 <= frame.selected_index < len(nodes)):
        return None
    node = nodes[frame.selected_index]

    if node.kind in ("movie", "show"):
        return node

    if node.kind == "episode":
        if node.grandparent_rating_key:
            return PlexNode(rating_key=node.grandparent_rating_key, title=node.series_title or node.title, kind="show", year=node.year)
        if node.series_title:
            return PlexNode(
                rating_key=f"series-title:{node.series_title.lower()}", title=node.series_title, kind="show", year=node.year
            )
        return None

    if node.kind == "season":
        for outer_frame in reversed(nav_stack[:-1]):
            outer_nodes = frame_nodes(outer_frame)
            if not (0 <= outer_frame.selected_index < len(outer_nodes)):
                return None
            outer_node = outer_nodes[outer_frame.selected_index]
            if outer_node.kind in ("movie", "show"):
                return outer_node
            if outer_node.kind != "season":
                return None
    return None


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
    tmdb_cache_dir: Path | None = DEFAULT_TMDB_CACHE_DIR,
    tmdb_cache_max_age: timedelta = DEFAULT_TMDB_CACHE_MAX_AGE,
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
    xtream_creds: XtreamCreds | None = None,
    series_root_nodes: list[SeriesNode] | None = None,
    plex_client_id: str | None = None,
    plex_activity_reporting: bool = True,
    plex_theme_music: bool = True,
    update_checker: Callable[[], UpdateInfo | None] | None = None,
    initial_vod_item: VodItem | None = None,
    vod_metadata_loader: Callable[[], VodItem | None] | None = None,
    full_screen: bool = True,
    glsl_shader: list[str] | None = None,
    interpolation: bool = False,
    chapter_skip: bool = True,
    skip_markers: bool = True,
    autoplay_next_episode: bool = True,
    autoplay_countdown_seconds: float = 10.0,
    audio_passthrough: bool = False,
    audio_downmix_boost: bool = False,
    loudness_normalization: bool = False,
    playlist_source: str | None = None,
    history_path: Path | None = None,
    tvtimes_watchlist_feed: TvtimesFeed | None = None,
    tvtimes_watch_report_feed: TvtimesFeed | None = None,
    tvtimes_device_name: str | None = None,
    tvtimes_web_feed: TvtimesFeed | None = None,
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
    if audio_passthrough:
        # Sends the encoded bitstream straight to an AVR/soundbar over
        # S/PDIF or HDMI instead of decoding it here -- only takes
        # effect when the output device actually supports it; mpv falls
        # back to normal decoding otherwise, same as leaving this unset.
        mpv_options["audio_spdif"] = "ac3,dts,eac3,truehd"
    if audio_downmix_boost:
        # mpv's own audio-normalize-downmix option: raises the center/
        # surround channels' volume when downmixing to stereo, so
        # dialogue and surround effects don't get quiet relative to the
        # front L/R channels the way a naive downmix leaves them.
        mpv_options["audio_normalize_downmix"] = True
    if loudness_normalization:
        # ffmpeg's loudnorm filter via mpv's lavfi bridge -- evens out
        # volume across a title (or between titles), confirmed live to
        # parse and apply cleanly as a startup af option.
        mpv_options["af"] = "lavfi=[loudnorm]"
    player = Player(fullscreen=full_screen, **mpv_options)
    hide_timer: threading.Timer | None = None
    # Resolves the (kind, id) TMDB link target for whatever the 'i'
    # overlay is currently showing -- set at the end of show_epg_overlay/
    # show_selected_details/show_vod_info_overlay's own render, called by
    # _open_info_overlay_tmdb_page when a second 'i' press arrives while
    # that same overlay is still on screen. A resolver closure rather
    # than a plain (kind, id) snapshot so a press that lands before the
    # relevant background prefetch/resolve (tmdb.prefetch_movie_id,
    # cli.py's own _resolve_plex_show_tmdb_id_in_background) has finished
    # still gets a correct answer -- each resolver only ever re-reads an
    # already-populated cache (never blocks), so calling it again on a
    # later press is always cheap and safe. One shared slot rather than
    # one per overlay: guide-open (details_visible) and live-viewing
    # (hide_timer) are mutually exclusive in practice, so nothing ever
    # actually shares this across two different overlays at once.
    info_overlay_tmdb_target_resolver: Callable[[], tuple[str, int] | None] | None = None
    # Which family of hide_timer-owning overlay currently holds it --
    # "recording"/"live_epg"/"vod_playing" (show_epg_overlay/
    # show_vod_info_overlay) or "plex_details" (show_vod_info_overlay's
    # Plex-selected-item popup). Reset to None in lockstep with hide_timer
    # itself (see cancel_hide_timer) so it never outlives the overlay it
    # names. Consulted by _on_vod_info_key (alongside
    # info_overlay_plex_rating_key below) to tell the Plex DETAILS popup
    # apart from every other overlay this could be -- see its own
    # _info_overlay_plex_selection_unchanged for why that distinction
    # matters.
    info_overlay_owner: str | None = None
    # The Plex node's rating_key the DETAILS popup above was last rendered
    # for -- None whenever info_overlay_owner isn't "plex_details". See
    # _on_vod_info_key's own comment for why this, rather than trusting
    # hide_timer/info_overlay_owner alone, is what actually decides
    # whether a second 'i' press here means "same item, open its TMDB
    # page" or "different item now selected, show its details instead".
    info_overlay_plex_rating_key: str | None = None
    resize_timer: threading.Timer | None = None
    guide_logo_refresh_timer: threading.Timer | None = None
    history_image_refresh_timer: threading.Timer | None = None
    plex_image_refresh_timer: threading.Timer | None = None
    series_image_refresh_timer: threading.Timer | None = None
    last_mouse_trigger = float("-inf")
    guide_visible = False
    guide_window_start: datetime | None = None
    selected_channel_url: str | None = None
    # Kept in sync with selected_channel_url everywhere it's assigned --
    # a (url, name) pair disambiguates the rare real playlist where two
    # distinct channels (e.g. an SD/HD pair) share the exact same stream
    # URL, which move_guide_selection/visible_guide_channels otherwise
    # can't tell apart (list.index/list "in" only ever finds the first
    # matching URL, which used to strand the guide's cursor permanently
    # bouncing back to that first row instead of advancing past it).
    selected_channel_name: str | None = None
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
    # Session-wide, not per-item (unlike skip_marker_shown/up_next_node
    # below) -- "put me to sleep in 30 minutes" should survive a channel
    # or VOD switch, same reasoning as live_pause_timer's own scope.
    sleep_timer_index = 0
    sleep_timer: threading.Timer | None = None
    pip_active = False
    recording_path: Path | None = None
    # Which marker's "Skip Intro"/"Skip Credits" prompt (see
    # _skip_marker_poll_loop) is currently on screen, if any -- doubles
    # as that loop's own "is it already showing" check, same as
    # plex_item_menu_node doubles for the Plex item menu.
    skip_marker_shown: VodMarker | None = None
    # The end-of-episode "Up Next" countdown's own state -- up_next_node
    # is also this prompt's own "is it showing" check, same pattern as
    # skip_marker_shown/plex_item_menu_node above. up_next_deadline is a
    # time.monotonic() timestamp (immune to wall-clock adjustments during
    # the countdown), not a plain seconds-remaining counter, so a slow
    # tick (system under load, etc.) still lands on the right real-world
    # moment rather than drifting late.
    up_next_node: PlexNode | None = None
    up_next_deadline: float | None = None
    up_next_timer: threading.Timer | None = None
    up_next_thumb: Image.Image | None = None
    # The chapter-scrub preview's own state (see preview_chapter) --
    # chapter_preview_index doubles as its own "is it showing" check,
    # same pattern as skip_marker_shown/up_next_node above. Index into
    # playing_vod_item.chapters, not a VodChapter itself, so moving the
    # preview is just clamped index arithmetic.
    chapter_preview_index: int | None = None
    chapter_preview_timer: threading.Timer | None = None
    guide_filter = ""
    filter_input_active = False
    filter_input_text = ""
    favorites_only = False
    favorites = favorites if favorites is not None else set()
    schedule_list = list(schedule) if schedule is not None else []
    active_schedule: ScheduledRecording | None = None
    schedule_stop_event = threading.Event()
    watchlist_stop_event = threading.Event()
    watch_report_stop_event = threading.Event()
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
    skip_marker_stop_event = threading.Event()
    schedule_browser_visible = False
    schedule_browser_selected_id: str | None = None
    help_visible = False
    help_tab_index = 0
    vod_visible = False
    vod_list: list[VodItem] = sort_vod_items(vod_items) if vod_items else []
    vod_selected_index = 0
    series_visible = False
    series_nav_stack: list[_SeriesNavFrame] = []
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
    # Whether letter/digit keys are currently shadowed for jump-nav --
    # only while the top of plex_nav_stack is a movie/show listing (see
    # _plex_frame_wants_jump_nav/_sync_plex_jump_bindings).
    plex_jump_bindings_active = False
    # Persists for the whole session (not reset per nav-stack level) --
    # same "toggle once, stays until toggled back" persistence as
    # plex_favorites_only itself, per the user's own requirement for this.
    plex_grid_view = True
    # The full-screen backdrop's TMDB title logo (see
    # render_and_show_plex/_plex_title_logo_target) -- resolved TMDB
    # logo URL, or a cached `None` for "looked, TMDB had nothing", keyed
    # by the relevant movie/show PlexNode's rating_key so a season or
    # episode listing shares its show's own single lookup rather than
    # re-resolving one per row. Process-lifetime, like every other TMDB
    # cache in this app -- never evicted.
    plex_title_logo_urls: dict[str, str | None] = {}
    plex_title_logo_in_flight: set[str] = set()
    # A Plex episode's own *show*-level TMDB id, for the "press i again to
    # view on TMDB" action (see show_vod_info_overlay) -- resolved_plex_
    # playable only fetches the episode's own metadata, whose Guid is a
    # different, episode-level id (confirmed live), not the /tv/{id}-
    # linkable show id this needs. Same process-lifetime, keyed-by-
    # rating_key (here, the episode's plex_grandparent_rating_key) shape
    # as plex_title_logo_urls above, resolved lazily in the background by
    # _resolve_plex_show_tmdb_id_in_background.
    plex_show_tmdb_ids: dict[str, int | None] = {}
    plex_show_tmdb_ids_in_flight: set[str] = set()
    # A fresh id per distinct Plex item played (see select_plex_node),
    # not per report -- report_plex_timeline needs the same session id
    # across repeated calls for one item so Plex treats them as one
    # ongoing session rather than a new one starting each time.
    plex_playback_session_id: str | None = None
    # The last position/duration _report_plex_state actually managed to
    # read -- see its own comment on why the final shutdown report needs
    # this fallback.
    plex_last_known_position: tuple[float, float] | None = None
    # Declared unconditionally (even for a non-Plex session, where it's
    # never actually constructed -- see the `if plex_creds is not None:`
    # block below) so the shared shutdown path can reference it and the
    # two timers below regardless of source type, the same reasoning
    # _report_plex_state itself is defined unconditionally for.
    plex_theme_player: PlexThemePlayer | None = None
    plex_theme_timer: threading.Timer | None = None  # debounce -- not yet started playing
    plex_theme_fade_timer: threading.Timer | None = None  # fade-out in progress
    plex_theme_current_rating_key: str | None = None  # the show actually playing, if any
    plex_theme_pending_key: str | None = None  # the show queued in plex_theme_timer, if any
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
        nonlocal hide_timer, info_overlay_owner
        if hide_timer is not None:
            hide_timer.cancel()
            hide_timer = None
            info_overlay_owner = None

    def _info_overlay_still_showing() -> bool:
        # hide_timer isn't reset to None when it fires naturally (only
        # cancel_hide_timer, called right before scheduling a fresh one,
        # does that) -- but threading.Timer is a Thread, so .is_alive()
        # correctly answers "hasn't fired or been cancelled yet" with no
        # extra state needed.
        return hide_timer is not None and hide_timer.is_alive()

    def _open_info_overlay_tmdb_page() -> None:
        target = info_overlay_tmdb_target_resolver() if info_overlay_tmdb_target_resolver is not None else None
        if target is None:
            player.show_text("No TMDB page available", duration_ms=2500)
            return
        kind, tmdb_id = target
        url = f"https://www.themoviedb.org/{kind}/{tmdb_id}"
        webbrowser.open(url)
        logger.info("Opened TMDB page: %s", url)

    def _open_in_tvtimes() -> None:
        """Hand whatever's on this channel back to the tvtimes web guide.

        The reverse of tvtimes' own Play button. Deliberately a *search* URL
        rather than a deep link to the guide cell: tvtimes' grid is virtualised,
        so pointing at one cell would need scroll-to-row support there, while
        `?q=<title>` needs nothing but the title we already have -- and finding
        the thing by name is what you actually want from the other end."""
        if tvtimes_web_feed is None:
            player.show_text("Not a tvtimes source", duration_ms=2500)
            return
        title: str | None = None
        if channel is not None:
            current, _upcoming = current_and_next_programmes(
                channel, epg, display, datetime.now(timezone.utc)
            )
            title = current.title if current is not None else None
        url = tvtimes_web_feed.base_url
        if title:
            url = f"{url}/search?q={urllib.parse.quote(title)}"
        webbrowser.open(url)
        logger.info("Opened tvtimes for %r", title or "the guide")
        player.show_text(
            f"Opened “{title}” in tvtimes" if title else "Opened tvtimes",
            duration_ms=3000,
        )

    def cancel_resize_timer() -> None:
        nonlocal resize_timer
        if resize_timer is not None:
            resize_timer.cancel()
            resize_timer = None

    def cancel_sleep_timer() -> None:
        nonlocal sleep_timer
        if sleep_timer is not None:
            sleep_timer.cancel()
            sleep_timer = None

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

    def cancel_series_image_refresh_timer() -> None:
        nonlocal series_image_refresh_timer
        if series_image_refresh_timer is not None:
            series_image_refresh_timer.cancel()
            series_image_refresh_timer = None

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

    def _sleep_timer_fire() -> None:
        # Runs on the threading.Timer's own background thread -- calling
        # player/nonlocal-mutating functions from a timer thread is
        # already an established-safe pattern in this file (the
        # skip-marker poll loop, the up-next countdown).
        nonlocal sleep_timer, sleep_timer_index
        sleep_timer = None
        sleep_timer_index = 0
        if not player.is_paused:
            # toggle_live_pause is the real pause path (live-buffer
            # auto-resume timer, Plex state reporting, the casting
            # guard, its own "Paused" OSD text) -- reused wholesale
            # rather than calling player.set_paused(True) directly and
            # re-deriving all of that.
            toggle_live_pause()
        logger.info("Sleep timer fired; playback paused")

    def cycle_sleep_timer() -> None:
        nonlocal sleep_timer_index, sleep_timer
        cancel_sleep_timer()
        sleep_timer_index = (sleep_timer_index + 1) % len(_SLEEP_TIMER_PRESETS_MINUTES)
        minutes = _SLEEP_TIMER_PRESETS_MINUTES[sleep_timer_index]
        if minutes == 0:
            player.show_text("Sleep timer: Off", duration_ms=2000)
            logger.info("Sleep timer -> Off")
            return
        sleep_timer = threading.Timer(minutes * 60, _sleep_timer_fire)
        sleep_timer.daemon = True
        sleep_timer.start()
        player.show_text(f"Sleep timer: {minutes}m", duration_ms=2000)
        logger.info("Sleep timer -> %dm", minutes)

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
            if series_visible:
                close_series_browser()
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

    def _current_chapter_index(chapters: list[VodChapter], position: float) -> int:
        # Used by preview_chapter to seed the preview cursor: which
        # chapter a given playback position currently falls within.
        current_index = 0
        for index, chapter in enumerate(chapters):
            if chapter.start_seconds <= position:
                current_index = index
            else:
                break
        return current_index

    def cancel_chapter_preview_timer() -> None:
        nonlocal chapter_preview_timer
        if chapter_preview_timer is not None:
            chapter_preview_timer.cancel()
            chapter_preview_timer = None

    def _chapter_preview_thumb_url(chapters: list[VodChapter], index: int) -> str | None:
        # Plex's own chapter thumbnail when it has one; otherwise a
        # locally-generated frame grab against whatever's actually
        # playing right now -- see VodChapter.thumb_url/
        # overlay.chapter_thumbnail_url's own docstrings for why this
        # fallback is resolved here, at render time, rather than stored
        # on the chapter itself.
        chapter = chapters[index]
        if chapter.thumb_url:
            return chapter.thumb_url
        if playing_vod_item is None:
            return None
        # Confirmed live (the "Streets of San Francisco" episode from
        # On Deck): a chapter's start_seconds routinely lands exactly on
        # a fade-to-black transition frame -- real TV-rip chapter
        # markers are commonly placed at commercial-break cuts, not mid-
        # scene. Grabbing the literal first frame there (extrema (0, 0)
        # confirmed via a direct capture) produces a thumbnail that's
        # technically present but indistinguishable from "no thumbnail
        # at all" against this overlay's own dark background. Nudging a
        # couple of seconds past the boundary reliably lands on real
        # content instead (same chapter, +2s: extrema (0, 255)). Only
        # applied to this local fallback -- a real Plex-provided thumb
        # (returned above) is server-generated from a properly chosen
        # frame and needs no such nudge. Clamped to stay within this
        # chapter -- never spill into the next one -- for a short
        # chapter close to its neighbour.
        next_start = chapters[index + 1].start_seconds if index + 1 < len(chapters) else None
        seek_seconds = chapter.start_seconds + _CHAPTER_THUMB_SEEK_OFFSET_SECONDS
        if next_start is not None:
            seek_seconds = min(seek_seconds, chapter.start_seconds + (next_start - chapter.start_seconds) / 2)
        return chapter_thumbnail_url(playing_vod_item.url, seek_seconds)

    def _prefetch_neighbor_chapter_thumbs(index: int) -> None:
        # Best-effort head start for wherever UP/DOWN goes next -- the
        # local-frame-grab fallback confirmed live to routinely take
        # several seconds (a second connection to a file the main
        # session is already streaming, worse yet over a debrid remote),
        # comfortably longer than a preview realistically stays up for,
        # so generating it reactively, only once actually previewed,
        # means it's rarely ready in time to ever be seen at all. Firing
        # this for both neighbors *every* time the cursor moves -- not
        # just once at playback start -- means continuing to browse
        # keeps the next likely stop a step ahead of you. No on_resolved
        # callback -- this never redraws anything itself, it just warms
        # _logo_cache so the next _render_chapter_preview (whenever that
        # happens to be) finds it already there via cached_image.
        if playing_vod_item is None or not playing_vod_item.chapters:
            return
        chapters = playing_vod_item.chapters
        urls = [
            _chapter_preview_thumb_url(chapters, neighbor)
            for neighbor in (index - 1, index + 1)
            if 0 <= neighbor < len(chapters)
        ]
        for url in urls:
            forget_failed_fetch(url)  # a stale failure shouldn't block trying again -- see its own docstring
        prefetch_images(urls)

    def _render_chapter_preview() -> None:
        if chapter_preview_index is None or playing_vod_item is None or not playing_vod_item.chapters:
            return
        chapters = playing_vod_item.chapters
        chapter = chapters[chapter_preview_index]
        title = chapter.title or f"Chapter {chapter_preview_index + 1}"
        thumb_url = _chapter_preview_thumb_url(chapters, chapter_preview_index)
        thumb_image = cached_image(thumb_url) if thumb_url else None
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_chapter_preview_overlay(title, thumb_image, osd_size[0], osd_size[1])
        x = (osd_size[0] - image.width) // 2
        edge_margin = round(osd_size[1] * 0.06)
        y = osd_size[1] - image.height - edge_margin
        player.show_overlay(image, x=x, y=y, overlay_id=_CHAPTER_PREVIEW_OVERLAY_ID)
        if thumb_url and thumb_image is None:
            target_index = chapter_preview_index

            def _on_thumb_resolved() -> None:
                # Only redraw if still previewing the *same* chapter --
                # the user may have already moved on, cancelled, or
                # committed by the time a slow fetch (especially the
                # local-frame-grab fallback) finishes.
                if chapter_preview_index == target_index:
                    _render_chapter_preview()

            prefetch_images([thumb_url], on_resolved=_on_thumb_resolved)

    def _restore_chapter_preview_keys() -> None:
        # ESC has no binding of its own during plain playback (confirmed
        # live -- every ESC binding elsewhere in this file is scoped to a
        # specific browser/overlay being open, which preview_chapter
        # never runs alongside, see sync_base_up_down_bindings' own
        # _any_browser_open guard), so unbinding it here is a full
        # restore, not a partial one.
        player.unbind_key("ESC")
        player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed below

    def _commit_chapter_preview() -> None:
        # The chapter-preview timer's own target *and* what ENTER is
        # rebound to while a preview is showing (see preview_chapter) --
        # actually performs the seek this whole state machine has been
        # deferring.
        nonlocal chapter_preview_index
        cancel_chapter_preview_timer()
        if chapter_preview_index is None or playing_vod_item is None or not playing_vod_item.chapters:
            chapter_preview_index = None
            return
        if _any_browser_open():
            # Some other browser (opened via a key preview_chapter never
            # touches, e.g. 'g'/'m') now legitimately owns ENTER/ESC --
            # restoring them here, or seeking out from under the user
            # while they're browsing something else entirely, would both
            # be wrong. Same guard the skip-marker poll loop already
            # uses for the same reason. The overlay clear is harmless
            # either way (already hidden behind that browser's own).
            chapter_preview_index = None
            player.clear_overlay(overlay_id=_CHAPTER_PREVIEW_OVERLAY_ID)
            return
        chapters = playing_vod_item.chapters
        target = chapters[chapter_preview_index]
        player.seek_to(target.start_seconds)
        player.clear_overlay(overlay_id=_CHAPTER_PREVIEW_OVERLAY_ID)
        _restore_chapter_preview_keys()
        chapter_preview_index = None
        label = target.title or f"Chapter {chapters.index(target) + 1}"
        logger.info("Jumped to chapter at %.0fs (%s)", target.start_seconds, label)

    def cancel_chapter_preview() -> None:
        # What ESC is rebound to while a preview is showing -- same
        # cleanup as _commit_chapter_preview, minus the seek.
        nonlocal chapter_preview_index
        cancel_chapter_preview_timer()
        if chapter_preview_index is None:
            return
        player.clear_overlay(overlay_id=_CHAPTER_PREVIEW_OVERLAY_ID)
        if _any_browser_open():
            # See _commit_chapter_preview's own comment -- some other
            # browser now legitimately owns ESC, don't restore over it.
            chapter_preview_index = None
            return
        _restore_chapter_preview_keys()
        chapter_preview_index = None

    def preview_chapter(direction: int) -> None:
        # What UP/DOWN become while chapters are available (see
        # sync_base_up_down_bindings) -- unlike the old immediate-seek
        # skip_to_chapter this replaced, this only moves a preview
        # cursor; the actual seek happens in _commit_chapter_preview,
        # either via ENTER or the idle-timeout auto-commit started
        # below. direction is +1/-1 for next/previous, same sense as
        # skip_to_chapter had (matches mpv's own default for these keys).
        nonlocal chapter_preview_index, chapter_preview_timer
        chapters = playing_vod_item.chapters if playing_vod_item is not None else None
        if not chapters:
            return
        if chapter_preview_index is None:
            # Seed the cursor from wherever actual playback currently
            # is, same DVD-remote-style "previous jumps to the current
            # chapter's own start unless already within
            # _CHAPTER_SKIP_BACK_THRESHOLD_SECONDS of it" logic
            # skip_to_chapter used to apply directly -- only relevant for
            # this first press, since every subsequent UP/DOWN while
            # already previewing just moves the cursor by one chapter.
            position_and_duration = player.playback_position()
            position = position_and_duration[0] if position_and_duration is not None else 0.0
            current_index = _current_chapter_index(chapters, position)
            if direction > 0:
                chapter_preview_index = min(len(chapters) - 1, current_index + 1)
            else:
                current_start = chapters[current_index].start_seconds
                if current_index == 0 or position - current_start > _CHAPTER_SKIP_BACK_THRESHOLD_SECONDS:
                    chapter_preview_index = current_index
                else:
                    chapter_preview_index = current_index - 1
            player.on_key_press("ENTER", _commit_chapter_preview)
            player.on_key_press("ESC", cancel_chapter_preview)
        else:
            chapter_preview_index = max(0, min(len(chapters) - 1, chapter_preview_index + direction))
        # A stale failure shouldn't block trying again -- see
        # forget_failed_fetch's own docstring. Deliberately only here
        # (a genuine new cursor move), not inside _render_chapter_preview
        # itself, which also re-runs from _on_thumb_resolved's own
        # redraw-on-resolve -- clearing the failure there too would
        # immediately requeue another attempt on every single failure,
        # an unthrottled retry loop with no cooldown at all.
        forget_failed_fetch(_chapter_preview_thumb_url(chapters, chapter_preview_index))
        _render_chapter_preview()
        _prefetch_neighbor_chapter_thumbs(chapter_preview_index)
        cancel_chapter_preview_timer()
        chapter_preview_timer = threading.Timer(_CHAPTER_PREVIEW_COMMIT_SECONDS, _commit_chapter_preview)
        chapter_preview_timer.daemon = True
        chapter_preview_timer.start()

    def _any_browser_open() -> bool:
        # Shared by sync_base_up_down_bindings and the skip-marker poll
        # loop below -- both need to know when no browser/overlay owns
        # the screen (and, for UP/DOWN, the keys themselves) for its own
        # navigation, so neither one fights whichever browser is
        # currently open for a different item's switch.
        return (
            guide_visible
            or recordings_visible
            or vod_visible
            or series_visible
            or schedule_browser_visible
            or history_browser_visible
            or chromecast_visible
            or plex_visible
            or plex_item_menu_node is not None
        )

    def sync_base_up_down_bindings() -> None:
        # The "resting" meaning of UP/DOWN for whatever's currently
        # playing, once no browser/overlay owns them for its own
        # navigation (every one of those restores this exact state
        # itself when it closes, same as they already do for ENTER via
        # toggle_live_pause -- see each one's own "restore the base
        # binding just removed above" comment). Also called from
        # handle_playback_started, since a new item's chapter
        # availability can change UP/DOWN's meaning with no browser
        # open/close involved at all (e.g. 'b'-switching channels, or a
        # reconnect).
        if _any_browser_open():
            return
        if chapter_skip and playing_vod_item is not None and playing_vod_item.chapters:
            # Matches mpv's own default sense for these keys (confirmed
            # live via player.input_bindings): UP seeks forward, DOWN
            # seeks backward -- so UP is next chapter, DOWN is previous.
            player.on_key_press("UP", lambda: preview_chapter(1))
            player.on_key_press("DOWN", lambda: preview_chapter(-1))
            # Warms the cache for whichever chapter a first UP/DOWN press
            # is likely to preview, same reasoning as
            # _prefetch_neighbor_chapter_thumbs's own comment -- safe (not
            # wasteful) to call every time this function runs, even
            # repeatedly for the same still-playing item, since
            # prefetch_images already skips anything already cached or
            # in-flight.
            position_and_duration = player.playback_position()
            position = position_and_duration[0] if position_and_duration is not None else 0.0
            _prefetch_neighbor_chapter_thumbs(_current_chapter_index(playing_vod_item.chapters, position))
        else:
            player.unbind_key("UP")
            player.unbind_key("DOWN")

    def _skip_marker_window() -> tuple[str, VodMarker] | None:
        # Whichever marker (see vod.VodMarker) the current playback
        # position falls inside, if any -- "intro" checked before
        # "credits" since the two windows never overlap in practice.
        # None when skip_markers is off, nothing's playing, the item has
        # neither marker (most items, even on a Plex Pass server -- see
        # this feature's own live-verification note), or position isn't
        # known yet.
        if not skip_markers or playing_vod_item is None:
            return None
        position_and_duration = player.playback_position()
        if position_and_duration is None:
            return None
        position = position_and_duration[0]
        intro = playing_vod_item.intro_marker
        if intro is not None and intro.start_seconds <= position < intro.end_seconds:
            return "intro", intro
        credits_marker = playing_vod_item.credits_marker
        if credits_marker is not None and credits_marker.start_seconds <= position < credits_marker.end_seconds:
            return "credits", credits_marker
        return None

    def hide_skip_marker_prompt() -> None:
        nonlocal skip_marker_shown
        if skip_marker_shown is None:
            return
        player.clear_overlay(overlay_id=_SKIP_MARKER_OVERLAY_ID)
        player.unbind_key("j")
        player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed below
        skip_marker_shown = None

    def confirm_skip_marker() -> None:
        # Only ever bound while the prompt is actually showing (see
        # _show_skip_marker_prompt/hide_skip_marker_prompt) -- a real
        # keypress, never automatic, same "nothing seeks on its own"
        # rule chapter-skip already follows. Bound to both 'j' and
        # ENTER -- the latter so an IR/BLE air-mouse remote's OK button
        # (which sends ENTER, not an arbitrary letter) can confirm it
        # too; ENTER's own base "toggle pause" meaning is shadowed only
        # for this prompt's duration, restored the moment it closes (see
        # hide_skip_marker_prompt) -- a remote's dedicated PLAY/PAUSE/
        # PLAYPAUSE buttons still pause normally regardless.
        if skip_marker_shown is None:
            return
        target_seconds = skip_marker_shown.end_seconds
        player.seek_to(target_seconds)
        logger.info("Skipped marker to %.0fs", target_seconds)
        hide_skip_marker_prompt()

    def _show_skip_marker_prompt(kind: str, marker: VodMarker) -> None:
        nonlocal skip_marker_shown
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_skip_marker_overlay(kind, osd_size[0], osd_size[1])
        edge_margin = round(osd_size[0] * 0.02)
        x = osd_size[0] - image.width - edge_margin
        y = osd_size[1] - image.height - edge_margin
        player.show_overlay(image, x=x, y=y, overlay_id=_SKIP_MARKER_OVERLAY_ID)
        player.on_key_press("j", confirm_skip_marker)
        player.on_key_press("ENTER", confirm_skip_marker)
        skip_marker_shown = marker

    def _skip_marker_poll_tick() -> None:
        if _any_browser_open():
            hide_skip_marker_prompt()
            return
        window = _skip_marker_window()
        if window is None:
            hide_skip_marker_prompt()
            return
        kind, marker = window
        if skip_marker_shown is not marker:
            _show_skip_marker_prompt(kind, marker)

    def _skip_marker_poll_loop() -> None:
        while True:
            try:
                _skip_marker_poll_tick()
            except Exception:
                logger.exception("Error while polling skip-intro/credits markers")
            if skip_marker_stop_event.wait(_SKIP_MARKER_POLL_SECONDS):
                return

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
        player.unbind_key("LEFT")
        player.unbind_key("RIGHT")
        help_visible = False
        logger.info("Help overlay closed")
        _reopen_plex_if_pending()

    def _render_and_show_help() -> None:
        osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
        image = render_help_overlay(osd_size[0], osd_size[1], tab_index=help_tab_index)
        x = (osd_size[0] - image.width) // 2
        y = (osd_size[1] - image.height) // 2
        player.show_overlay(image, x=x, y=y, overlay_id=_HELP_OVERLAY_ID)

    def _prev_help_tab() -> None:
        nonlocal help_tab_index
        help_tab_index = (help_tab_index - 1) % help_tab_count()
        _render_and_show_help()

    def _next_help_tab() -> None:
        nonlocal help_tab_index
        help_tab_index = (help_tab_index + 1) % help_tab_count()
        _render_and_show_help()

    def open_help_overlay() -> None:
        nonlocal help_visible, help_tab_index
        help_tab_index = 0  # always starts on the first tab, not wherever it was left last time
        _render_and_show_help()
        player.on_key_press("ESC", close_help_overlay)
        player.on_key_press("LEFT", _prev_help_tab)
        player.on_key_press("RIGHT", _next_help_tab)
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
        if series_visible:
            close_series_browser()
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
        if series_visible:
            close_series_browser()
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
        sync_base_up_down_bindings()
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
        nonlocal channel, playing_recording, playing_vod_item, last_channel, plex_reopen_pending
        if not history_browser_visible or not history_browser_list:
            return
        entry = history_browser_list[history_browser_selected_index]

        if entry.kind == "recording":
            path = Path(entry.url)
            if not path.is_file():
                close_history_browser()
                player.show_text(f"'{entry.title}' no longer exists", duration_ms=3000)
                return
            # About to actually start playback -- suppress the Plex-browser
            # reopen close_history_browser() would otherwise trigger (see
            # plex_reopen_pending's own comment), which stole focus back
            # from Plex only for peeking at history, not for playing
            # something instead; confirmed live that without this, a
            # replayed item started playing behind an unwanted, still-open
            # Plex browser overlay.
            plex_reopen_pending = False
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
            plex_reopen_pending = False  # see the recording branch's own comment above
            close_history_browser()
            switch_to_channel(matched)
            return

        if entry.kind == "vod":
            plex_reopen_pending = False  # see the recording branch's own comment above
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
                logger.info("Resuming VOD item from history at %.0fs: %s", resume_at, redact_resource_url(item.url))
            else:
                player.show_text(f"Playing: {item.title}", duration_ms=3000)
                logger.info("Replaying VOD item from history: %s", redact_resource_url(item.url))
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
        if series_visible:
            close_series_browser()
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
            save_playback_positions(playback_positions_path, playback_positions, touched_key=key)
        except OSError as exc:
            logger.warning("Could not save playback position to %s: %s", playback_positions_path, exc)

    def _save_current_vod_position() -> None:
        # Same as _save_current_recording_position, but for whatever VOD
        # movie is currently playing -- keyed by its stream URL rather than
        # a local file path (see playback_positions.save_playback_positions,
        # which ages a remote key out after DEFAULT_PLAYBACK_POSITION_MAX_AGE
        # of nobody resuming/updating it, since there's no local file to
        # prune it by existence the way a recording's own key is).
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
            save_playback_positions(playback_positions_path, playback_positions, touched_key=key)
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

    def cancel_plex_theme_timer() -> None:
        nonlocal plex_theme_timer
        if plex_theme_timer is not None:
            plex_theme_timer.cancel()
            plex_theme_timer = None

    def cancel_plex_theme_fade_timer() -> None:
        nonlocal plex_theme_fade_timer
        if plex_theme_fade_timer is not None:
            plex_theme_fade_timer.cancel()
            plex_theme_fade_timer = None

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

    def _resolve_plex_show_tmdb_id_in_background(grandparent_rating_key: str) -> None:
        # Background-resolves a Plex episode's *show*-level TMDB id (see
        # plex_show_tmdb_ids' own comment) for _vod_item_tmdb_target below
        # -- fire-and-forget, unlike _resolve_plex_title_logo_in_background's
        # redraw-on-resolve: this only matters the next time the 'i'
        # overlay is actually shown, not for anything already on screen.
        if (
            plex_creds is None
            or grandparent_rating_key in plex_show_tmdb_ids
            or grandparent_rating_key in plex_show_tmdb_ids_in_flight
        ):
            return
        plex_show_tmdb_ids_in_flight.add(grandparent_rating_key)

        def _fetch() -> None:
            try:
                plex_show_tmdb_ids[grandparent_rating_key] = plex_show_tmdb_id(plex_creds, grandparent_rating_key)
            finally:
                plex_show_tmdb_ids_in_flight.discard(grandparent_rating_key)

        threading.Thread(target=_fetch, daemon=True).start()

    def _vod_item_tmdb_target(item: VodItem) -> tuple[str, int] | None:
        # The (kind, id) target for _open_info_overlay_tmdb_page, given
        # whatever VodItem show_vod_info_overlay just rendered -- a
        # movie's own tmdb_id (Plex or local-file/YouTube, see
        # VodItem.tmdb_id's own docstring) directly, or a Plex episode's
        # *show*-level id via the background-resolved plex_show_tmdb_ids
        # cache. None for anything else (Xtream/Stalker/M3U-split VOD, or
        # a show-id lookup still in flight).
        if item.tmdb_id is not None:
            return ("movie", item.tmdb_id)
        if item.plex_grandparent_rating_key is not None:
            key = item.plex_grandparent_rating_key
            if key not in plex_show_tmdb_ids:
                _resolve_plex_show_tmdb_id_in_background(key)
                return None
            show_id = plex_show_tmdb_ids[key]
            return ("tv", show_id) if show_id is not None else None
        return None

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
        nonlocal hide_timer, info_overlay_tmdb_target_resolver, info_overlay_owner, info_overlay_plex_rating_key
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
                    info_overlay_tmdb_target_resolver = lambda item=item: _vod_item_tmdb_target(item)
                    info_overlay_owner = "plex_details"
                    info_overlay_plex_rating_key = node.rating_key
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
        stream_info = player.stream_info()
        image = render_vod_info_overlay(
            playing_vod_item,
            osd_size[0],
            osd_size[1],
            position_seconds=position,
            duration_seconds=duration,
            stream_info=stream_info,
        )
        x = (osd_size[0] - image.width) // 2
        y = (osd_size[1] - image.height) // 2
        player.show_overlay(image, x=x, y=y)
        info_overlay_tmdb_target_resolver = lambda item=playing_vod_item: _vod_item_tmdb_target(item)
        info_overlay_owner = "vod_playing"
        info_overlay_plex_rating_key = None
        hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
        hide_timer.daemon = True
        hide_timer.start()

    def _info_overlay_plex_selection_unchanged() -> bool:
        # True unless the currently-showing overlay is the Plex DETAILS
        # popup (see show_vod_info_overlay) *and* the browser's selection
        # has since moved to a different item -- covers every way that can
        # happen (arrow-key movement, search, year filter, drilling in or
        # back) without needing a dedicated fix at each of those call
        # sites: this just compares "what's selected now" against
        # info_overlay_plex_rating_key, recorded when the popup was drawn.
        if info_overlay_owner != "plex_details":
            return True
        if not (plex_visible and plex_nav_stack):
            return False
        frame = plex_nav_stack[-1]
        nodes = plex_frame_nodes(frame)
        if not nodes:
            return False
        return nodes[frame.selected_index].rating_key == info_overlay_plex_rating_key

    def _on_vod_info_key() -> None:
        # The actual 'i'/MENU key binding everywhere show_vod_info_overlay
        # is reachable directly (Plex session, local-file/YouTube session)
        # -- every internal call to show_vod_info_overlay() (show_epg_overlay's
        # own VOD delegation, the on-pause auto-show, the hero-art-ready
        # redraw) calls it directly instead of through here, so a repeated
        # *automatic* redraw of fresh content is never swallowed by the
        # check below -- only a genuine repeated keypress is.
        if _info_overlay_still_showing() and _info_overlay_plex_selection_unchanged():
            _open_info_overlay_tmdb_page()
            return
        show_vod_info_overlay()

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
        sync_base_up_down_bindings()
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
        if series_visible:
            close_series_browser()
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
        if series_visible:
            close_series_browser()
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
            logger.error("Failed to play %s (%s)", label, redact_resource_url(target_url))
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
        sync_base_up_down_bindings()
        hide_skip_marker_prompt()  # a stale prompt from whatever was playing before shouldn't survive a switch
        cancel_chapter_preview()  # ditto for a stale chapter preview -- see its own comment
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

    logger.info("Starting playback: %s (%s)", title or url, redact_resource_url(url))
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
                logger.info("Resuming at %.0fs: %s", resume_at, redact_resource_url(url))
        # A Plex session has nothing to play yet -- force_window (see
        # Player.__init__) keeps the window/input alive with nothing
        # loaded, exactly as it already does for a failed direct-stream
        # URL; the Plex browser (opened further below) is what puts
        # something on screen for the user to actually pick.
        player.on_key_press("z", cycle_aspect_ratio)  # available for any playback, not just EPG-backed channels
        player.on_key_press("e", cycle_sleep_timer)  # ditto
        player.on_key_press("r", toggle_recording)  # ditto
        player.on_key_press("?", toggle_help_overlay)  # ditto
        player.on_key_press("p", toggle_live_pause)  # ditto
        player.on_key_press("o", toggle_picture_in_picture)  # ditto
        player.on_key_press("t", toggle_subtitles)  # ditto
        player.on_key_press("a", toggle_about_overlay)  # ditto
        # Shifted so it doesn't collide with 't' (subtitles) -- opens the
        # companion tvtimes web guide for whatever's on now.
        player.on_key_press("T", _open_in_tvtimes)  # ditto
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
        # Started unconditionally (like the autosave thread above) rather
        # than only while a markered item is playing -- _skip_marker_window
        # already no-ops cheaply whenever skip_markers is off or the
        # current item has no markers, so there's no per-item lifecycle to
        # manage the way sync_base_up_down_bindings has for UP/DOWN.
        skip_marker_thread = threading.Thread(target=_skip_marker_poll_loop, daemon=True)
        skip_marker_thread.start()

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
            player.on_key_press("i", _on_vod_info_key)
            player.on_key_press("MENU", _on_vod_info_key)

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
                    logger.info("No TMDB metadata found for %s", title or redact_resource_url(url))

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
                    logo_url = item.logo_url or fetch_tv_logo_cached(
                        item.series_title, item.year, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                    )
                else:
                    metadata = fetch_movie_metadata_cached(
                        item.title, item.year, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                    )
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
                        adopt_epg_shift_policy(display, epg)
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
                nonlocal hide_timer, info_overlay_tmdb_target_resolver, info_overlay_owner
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
                    info_overlay_tmdb_target_resolver = lambda: None  # a local recording has no TMDB identity of any kind
                    info_overlay_owner = "recording"
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
                stream_info = player.stream_info()
                badges = stream_quality_badges(stream_info)
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
                    stream_info=stream_info,
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

                info_overlay_owner = "live_epg"

                def _live_epg_tmdb_target(programme=current, group_title=channel.group_title) -> tuple[str, int] | None:
                    if programme is None:
                        return None
                    movie_id = movie_id_for(programme.title, programme.category, programme.year, group_title)
                    return ("movie", movie_id) if movie_id is not None else None

                info_overlay_tmdb_target_resolver = _live_epg_tmdb_target
                hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
                hide_timer.daemon = True
                hide_timer.start()

                if tmdb_api_token is not None and current is not None and is_movie_category(current.category, channel.group_title):
                    # Same non-blocking pattern as render_and_show_guide's own
                    # prefetch -- this draw above already used whatever was
                    # cached; kicking this off just means the banner picks up
                    # the rating on its next show (channel switch, or 'i').
                    prefetch_ratings({(current.title, current.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age)
                    # Populates movie_id_for's cache the same lazy,
                    # single-item way -- for the "press i again to view on
                    # TMDB" action (see _on_epg_info_key), not this draw's
                    # own display (movie_id_for's result isn't rendered
                    # anywhere, unlike rating/director above).
                    prefetch_movie_id({(current.title, current.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age)
                    # Skipped when the feed's own <credits><director> already
                    # gave render_epg_overlay one (see show_selected_details's
                    # identical guard for the guide's details popup).
                    if not current.director:
                        prefetch_director(
                            {(current.title, current.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                        )
                    # Skipped when the feed's own <date> already gave
                    # render_epg_overlay/_render_epg_hero a year to show
                    # (see overlay.py's _title_with_year fallback_year
                    # param) -- same "don't fetch what's already known"
                    # guard as director above. Confirmed live: some feeds
                    # (a FastChannels-generated Plex TV guide) never
                    # populate <date> at all, for any programme, leaving
                    # every movie's hero/banner title without a year.
                    if not current.year:
                        prefetch_release_year(
                            {(current.title, current.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                        )
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

                    prefetch_backdrop(
                        {backdrop_key},
                        tmdb_api_token,
                        tmdb_cache_dir,
                        tmdb_cache_max_age,
                        on_fetched=_redraw_once_backdrop_ready,
                    )
                    prefetch_logo(
                        {backdrop_key},
                        tmdb_api_token,
                        tmdb_cache_dir,
                        tmdb_cache_max_age,
                        on_fetched=_redraw_once_backdrop_ready,
                    )

            def _on_epg_info_key() -> None:
                # The actual 'i'/MENU key binding for a channel session --
                # every *internal* call to show_epg_overlay() (channel
                # switch, resize/mouse-move redraw, on-pause auto-show, the
                # heart-toggle redraw, the backdrop-ready redraw above)
                # calls it directly instead of through here, so none of
                # those get swallowed by the check below -- only a
                # genuine repeated keypress does. guide_visible/
                # recordings_visible are excluded so 'i' still reaches
                # show_epg_overlay's own delegation to show_selected_details
                # (guide open) or no-op (recordings browser open) exactly
                # as before.
                if not guide_visible and not recordings_visible and _info_overlay_still_showing():
                    _open_info_overlay_tmdb_page()
                    return
                show_epg_overlay()

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
                nonlocal guide_filter, favorites_only
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                channel_list = guide_channel_list()
                cleared_message = None
                if not channel_list and (channels or [channel]) and (guide_filter or favorites_only):
                    # The filter emptied out an otherwise non-empty guide --
                    # the same "everything downstream is now permanently
                    # unresponsive" dead end already fixed for the Plex
                    # browser's own favorites-only filter (see
                    # render_and_show_plex): move_guide_selection's
                    # pool-emptiness guard means arrow keys/ENTER would
                    # otherwise silently do nothing forever, since nothing
                    # else ever re-applies the filter to a
                    # differently-shaped channel list later. Clear
                    # whichever filter(s) are responsible and fall back to
                    # what's left instead.
                    if guide_filter:
                        cleared_message = f"No channels match filter: {guide_filter!r}"
                        guide_filter = ""
                        channel_list = guide_channel_list()
                    if not channel_list and favorites_only:
                        cleared_message = "No favorited channels"
                        favorites_only = False
                        channel_list = guide_channel_list()
                    reset_guide_selection()
                image = render_program_guide(
                    channel_list,
                    epg,
                    display,
                    datetime.now(timezone.utc),
                    current_channel_url=channel.url,
                    canvas_width=osd_size[0],
                    canvas_height=osd_size[1],
                    window_start=guide_window_start,
                    max_rows=_GUIDE_MAX_ROWS,
                    selected_channel_url=selected_channel_url,
                    selected_channel_name=selected_channel_name,
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

                if cleared_message:
                    player.show_text(cleared_message, duration_ms=3000)

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
                    visible_guide_channels(
                        channel_list,
                        epg,
                        selected_channel_url or channel.url,
                        max_rows=_GUIDE_MAX_ROWS,
                        current_channel_name=selected_channel_name,
                    ),
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
                        channel_list,
                        epg,
                        display,
                        datetime.now(timezone.utc),
                        window_start=guide_window_start,
                        max_rows=_GUIDE_MAX_ROWS,
                        current_channel_url=channel.url,
                        selected_channel_url=selected_channel_url,
                    )
                    prefetch_ratings(movies, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age)

                return True

            def shift_guide(step: timedelta) -> None:
                nonlocal guide_window_start
                if not guide_visible or details_visible:
                    return  # LEFT/RIGHT are only rebound while the guide is open
                guide_window_start = resolved_guide_window_start() + step
                render_and_show_guide()
                logger.info("Guide window shifted by %s", step)

            def move_guide_selection(step: int) -> None:
                nonlocal selected_channel_url, selected_channel_name
                if not guide_visible or details_visible:
                    return
                # The full eligible list, not just the currently visible
                # window -- otherwise the cursor clamps at the edge of the
                # displayed rows instead of scrolling the guide to reveal
                # channels further down (or up) the list.
                pool = guide_eligible_channels(guide_channel_list(), epg)
                if not pool:
                    return
                # Matched by (url, name), not url alone -- some real
                # playlists reuse the exact same stream URL for a
                # channel's SD and HD listing (confirmed live), which
                # `list.index` can't tell apart (it always finds the
                # first matching URL, regardless of which row was
                # actually selected) -- this used to strand the cursor
                # permanently bouncing back to that first row instead of
                # ever advancing past it. Falls back to a url-only match
                # if the name doesn't line up (e.g. right after switching
                # to a channel via 'b'/direct URL, before a name's ever
                # been recorded).
                try:
                    index = next(
                        i for i, c in enumerate(pool) if c.url == selected_channel_url and c.name == selected_channel_name
                    )
                except StopIteration:
                    try:
                        index = next(i for i, c in enumerate(pool) if c.url == selected_channel_url)
                    except StopIteration:
                        index = 0
                selected = pool[max(0, min(len(pool) - 1, index + step))]
                selected_channel_url = selected.url
                selected_channel_name = selected.name
                render_and_show_guide()
                logger.info("Guide selection -> '%s'", selected.name)

            def nudge_selected_shift(step: timedelta) -> None:
                if not guide_visible or details_visible or selected_channel_url is None:
                    return  # '[' / ']' are only rebound while the guide is open, like the other guide keys
                selected_channel = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                if selected_channel is None:
                    return

                if display.guide_already_corrected:
                    # Storing one would be worse than useless: it does nothing
                    # here and silently double-shifts the same channel when
                    # watched direct from its provider.
                    player.show_text(
                        "This guide already carries its clock correction", duration_ms=2500
                    )
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
                nonlocal selected_channel_url, selected_channel_name
                # Called after the eligible channel list changes shape (a
                # filter applied/cleared) -- keeps the playing channel
                # selected if it's still eligible, else falls back to
                # whatever's first, mirroring toggle_guide's initial pick.
                pool = guide_eligible_channels(guide_channel_list(), epg)
                urls = [c.url for c in pool]
                selected = channel if channel.url in urls else (pool[0] if pool else None)
                selected_channel_url = selected.url if selected else None
                selected_channel_name = selected.name if selected else None

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

            def rebind_channel_base_letter_keys() -> None:
                # The always-on channel-mode letter bindings -- restored
                # wherever something else (the guide filter prompt, VOD
                # browser jump-nav) shadows all of a-z0-9 for its own use
                # and needs to hand them back afterward.
                player.on_key_press("g", toggle_guide)
                player.on_key_press("i", _on_epg_info_key)
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("e", cycle_sleep_timer)
                player.on_key_press("h", toggle_favorite)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("w", toggle_recordings_browser)
                player.on_key_press("u", toggle_schedule_browser)
                player.on_key_press("m", toggle_vod_browser)
                player.on_key_press("l", toggle_series_browser)
                player.on_key_press("b", switch_to_last_channel)
                player.on_key_press("p", toggle_live_pause)
                player.on_key_press("o", toggle_picture_in_picture)
                player.on_key_press("t", toggle_subtitles)
                player.on_key_press("a", toggle_about_overlay)
                player.on_key_press("k", toggle_chromecast_picker)
                player.on_key_press("x", toggle_history_browser)

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
                rebind_channel_base_letter_keys()
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
                nonlocal details_visible, details_channel, details_programme, info_overlay_tmdb_target_resolver
                if not guide_visible or selected_channel_url is None:
                    return
                if details_visible:
                    # A second 'i' press while the popup's already up (see
                    # _on_epg_info_key's identical reasoning for the
                    # hide_timer family) -- guide navigation is fully
                    # blocked while details_visible is True (every guide
                    # movement handler guards on it), so details_programme
                    # can never go stale underneath this popup the way it
                    # could for move_plex_selection's DETAILS overlay.
                    _open_info_overlay_tmdb_page()
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

                def _guide_details_tmdb_target(programme=programme, group_title=selected_channel.group_title) -> tuple[str, int] | None:
                    movie_id = movie_id_for(programme.title, programme.category, programme.year, group_title)
                    return ("movie", movie_id) if movie_id is not None else None

                info_overlay_tmdb_target_resolver = _guide_details_tmdb_target

                if tmdb_api_token is not None and is_movie_category(programme.category, selected_channel.group_title):
                    # Same non-blocking pattern as render_and_show_guide's own
                    # prefetch -- this draw above already used whatever was
                    # cached; kicking this off just means a repeat view (or
                    # the guide) picks up the rating soon after.
                    prefetch_ratings(
                        {(programme.title, programme.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                    )
                    # Populates movie_id_for's cache the same lazy,
                    # single-item way -- for the "press i again to view on
                    # TMDB" action above, not this draw's own display.
                    prefetch_movie_id(
                        {(programme.title, programme.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                    )
                    # Director isn't bulk-prefetched for every visible grid
                    # movie the way rating is (see tmdb._director_cache's own
                    # comment) -- only kicked off here, for the one programme
                    # whose details were actually opened. Skipped entirely
                    # when the feed's own <credits><director> already gave
                    # render_programme_details one (see overlay.py) -- no
                    # point spending a TMDB request on a field we're not
                    # even going to show.
                    if not programme.director:
                        prefetch_director(
                            {(programme.title, programme.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                        )
                    # Same "don't fetch what's already known" guard as
                    # director above -- see overlay.py's _title_with_year
                    # fallback_year param / render_programme_details.
                    if not programme.year:
                        prefetch_release_year(
                            {(programme.title, programme.year)}, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                        )
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
                sync_base_up_down_bindings()
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
                logger.info("Switched to channel '%s' (%s)", channel.name, redact_resource_url(channel.url))

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

            def _watchlist_poll_tick() -> None:
                if tvtimes_watchlist_feed is None:
                    return
                nonlocal schedule_list
                entries, error = fetch_tvtimes_watchlist(tvtimes_watchlist_feed)
                if error:
                    # Transient by nature (the server may be down, or this box
                    # off the network) -- log and try again next tick rather
                    # than dropping recordings we already scheduled.
                    logger.warning("tvtimes watchlist poll failed: %s", error)
                    return
                updated, added, removed = watchlist_schedule_updates(schedule_list, entries)
                if not added and not removed:
                    return
                schedule_list = updated
                _persist_schedule()
                logger.info(
                    "tvtimes watchlist: %d scheduled, %d dropped (%d entries in feed)",
                    added,
                    removed,
                    len(entries),
                )
                if added:
                    player.show_text(
                        f"Scheduled {added} recording{'s' if added != 1 else ''} from your tvtimes watchlist",
                        duration_ms=4000,
                    )
                if schedule_browser_visible:
                    render_and_show_schedule()

            def _watchlist_poll_loop() -> None:
                while True:
                    try:
                        _watchlist_poll_tick()
                    except Exception:
                        # Same reasoning as the schedule loop above: an
                        # uncaught exception here would silently stop every
                        # future watchlist sync for the rest of the run.
                        logger.exception("Error while syncing the tvtimes watchlist")
                    if watchlist_stop_event.wait(_WATCHLIST_POLL_SECONDS):
                        return

            if tvtimes_watchlist_feed is not None:
                threading.Thread(target=_watchlist_poll_loop, daemon=True).start()

            def _watch_report_tick() -> None:
                if tvtimes_watch_report_feed is None or history_path is None:
                    return
                entries, warnings = load_history(history_path)
                for warning in warnings:
                    logger.warning("watch report: %s", warning)
                events = watch_events_payload(
                    tvtimes_watch_report_feed,
                    entries,
                    device=tvtimes_device_name,
                    since=datetime.now(timezone.utc) - _WATCH_REPORT_WINDOW,
                )
                if not events:
                    return
                stored, error = post_watch_events(tvtimes_watch_report_feed, events)
                if error:
                    # The window is resent every tick, so a failed report costs
                    # nothing but a delay -- log and move on.
                    logger.warning("tvtimes watch report failed: %s", error)
                    return
                logger.info("tvtimes watch report: %d/%d intervals accepted", stored, len(events))

            def _watch_report_loop() -> None:
                while True:
                    try:
                        _watch_report_tick()
                    except Exception:
                        # Same reasoning as the other two loops: an uncaught
                        # exception here would silently stop all further
                        # reporting for the rest of the run.
                        logger.exception("Error while reporting watch state to tvtimes")
                    if watch_report_stop_event.wait(_WATCH_REPORT_SECONDS):
                        return

            if tvtimes_watch_report_feed is not None:
                threading.Thread(target=_watch_report_loop, daemon=True).start()

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
                sync_base_up_down_bindings()
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
                if series_visible:
                    close_series_browser()
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
                for char in _FILTER_INPUT_CHARS:
                    player.unbind_key(char)
                rebind_channel_base_letter_keys()
                vod_visible = False
                vod_selected_index = 0
                sync_base_up_down_bindings()
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

            def jump_vod_selection(letter: str) -> None:
                # Any letter/digit while the VOD browser is open jumps to
                # the next title starting with it -- vod_list is sorted
                # alphabetically within each group_title block (see
                # sort_vod_items), so repeated presses of the same letter
                # cycle through every match in that block before moving on.
                nonlocal vod_selected_index
                if not vod_visible or not vod_list:
                    return
                target = jump_to_letter_index([item.title for item in vod_list], vod_selected_index, letter)
                if target is not None:
                    vod_selected_index = target
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
                    logger.info("Resuming VOD item at %.0fs: %s", resume_at, redact_resource_url(selected.url))
                else:
                    player.show_text(f"Playing: {selected.title}", duration_ms=3000)
                    logger.info("Playing VOD item: %s", redact_resource_url(selected.url))

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
                    for char in _FILTER_INPUT_CHARS:
                        player.on_key_press(char, lambda char=char: jump_vod_selection(char))
                    logger.info("VOD browser opened (%d items)", len(vod_list))

            def toggle_vod_browser() -> None:
                if vod_visible:
                    close_vod_browser()
                    return
                if series_visible:
                    close_series_browser()
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

            # Source-agnostic references so the Series browser closures
            # below never branch on which source it is -- mirrors the
            # same trick plex_creds/list_plex_node_children already play
            # for the Plex browser. Only Xtream feeds this today; a second
            # source (Stalker) is meant to slot in here as another branch
            # once its portal 'series' API is verified (see the
            # series-stalker-wip branch). None (and series_root_nodes
            # empty, from main()) whenever no series source is in play, so
            # open_series_browser's "No TV series found" guard below is
            # the only thing that ever runs in that case.
            if xtream_creds is not None:

                def list_series_children(
                    node: SeriesNode | None, timeout: float = 15
                ) -> tuple[list[SeriesNode], str | None]:
                    return list_xtream_series_children(xtream_creds, node, timeout)

                def resolve_series_episode(node: SeriesNode, timeout: float = 15) -> tuple[VodItem | None, str | None]:
                    return resolve_xtream_series_episode(xtream_creds, node, timeout)

            else:
                list_series_children = None
                resolve_series_episode = None

            def _render_series_from_image_refresh_timer() -> None:
                # The timer's own target rather than render_and_show_series
                # directly, so series_visible is rechecked right before
                # actually rendering -- same reasoning as
                # _render_history_from_image_refresh_timer.
                nonlocal series_image_refresh_timer
                series_image_refresh_timer = None
                if series_visible:
                    render_and_show_series()

            def _on_series_image_resolved() -> None:
                # Runs on the resolving background thread (see
                # overlay.prefetch_images), potentially once per row on
                # the page -- debounced into a single re-render, same
                # reasoning as _on_history_image_resolved.
                nonlocal series_image_refresh_timer
                if not series_visible:
                    return
                cancel_series_image_refresh_timer()
                series_image_refresh_timer = threading.Timer(
                    _GUIDE_LOGO_REFRESH_DEBOUNCE_SECONDS, _render_series_from_image_refresh_timer
                )
                series_image_refresh_timer.daemon = True
                series_image_refresh_timer.start()

            def close_series_browser() -> None:
                nonlocal series_visible
                if not series_visible:
                    return
                cancel_series_image_refresh_timer()
                player.clear_overlay(overlay_id=_SERIES_OVERLAY_ID)
                player.unbind_key("UP")
                player.unbind_key("DOWN")
                player.unbind_key("PGUP")
                player.unbind_key("PGDWN")
                player.unbind_key("ENTER")
                player.unbind_key("KP_ENTER")
                player.unbind_key("ESC")
                player.unbind_key("LEFT")
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
                rebind_channel_base_letter_keys()
                series_visible = False
                sync_base_up_down_bindings()
                logger.info("Series browser closed")

            def render_and_show_series() -> bool:
                if not series_nav_stack:
                    return False
                frame = series_nav_stack[-1]
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_series_browser(
                    frame.breadcrumb,
                    frame.nodes,
                    frame.selected_index,
                    osd_size[0],
                    osd_size[1],
                    max_rows=_SERIES_MAX_ROWS,
                )
                if image is None:
                    return False
                x = (osd_size[0] - image.width) // 2
                y = max(0, osd_size[1] - image.height - _GUIDE_BOTTOM_MARGIN)
                player.show_overlay(image, x=x, y=y, overlay_id=_SERIES_OVERLAY_ID)
                # Never blocking: only spawns background fetches for
                # thumbnails not already cached/in-flight -- the image
                # just rendered above already used cached_image's
                # cache-only read, same reasoning as render_and_show_plex.
                visible = visible_series_nodes(frame.nodes, frame.selected_index, max_rows=_SERIES_MAX_ROWS)
                prefetch_images((node.poster_url for node in visible), on_resolved=_on_series_image_resolved)
                return True

            def move_series_selection(step: int) -> None:
                if not series_visible or not series_nav_stack:
                    return
                frame = series_nav_stack[-1]
                if not frame.nodes:
                    return
                frame.selected_index = max(0, min(len(frame.nodes) - 1, frame.selected_index + step))
                render_and_show_series()

            def _play_series_node(node: SeriesNode) -> None:
                nonlocal playing_recording, playing_vod_item
                if resolve_series_episode is None:
                    return
                player.show_text("Loading...", duration_ms=2000)
                item, error = resolve_series_episode(node)
                if item is None:
                    player.show_text(f"Series error: {error}", duration_ms=4000)
                    logger.error("Series error resolving '%s': %s", node.title, error)
                    return
                close_series_browser()
                _save_current_recording_position()  # in case we were already watching a recording
                _save_current_vod_position()  # in case we were already watching a different VOD item
                _end_current_history_entry()
                _reset_reconnect_state()
                playing_recording = None
                playing_vod_item = item
                _enrich_vod_hero_art_in_background(item)
                resume_at = playback_positions.get(item.url)
                player.play(item.url, title=item.title, start=resume_at)
                _start_history_entry("vod", item.title, item.url)
                if resume_at:
                    player.show_text(f"Resuming: {item.title}", duration_ms=3000)
                    logger.info("Resuming series episode at %.0fs: %s", resume_at, redact_resource_url(item.url))
                else:
                    player.show_text(f"Playing: {item.title}", duration_ms=3000)
                    logger.info("Playing series episode: %s", redact_resource_url(item.url))

            def select_series_node() -> None:
                if not series_visible or not series_nav_stack or list_series_children is None:
                    return
                frame = series_nav_stack[-1]
                if not frame.nodes:
                    return
                node = frame.nodes[frame.selected_index]
                if node.container:
                    player.show_text("Loading...", duration_ms=2000)
                    children, error = list_series_children(node)
                    if error:
                        player.show_text(f"Series error: {error}", duration_ms=4000)
                        logger.error("Series error listing '%s': %s", node.title, error)
                        return
                    if not children:
                        player.show_text(f"Nothing found in '{node.title}'", duration_ms=3000)
                        return
                    series_nav_stack.append(_SeriesNavFrame(breadcrumb=node.title, nodes=children))
                    render_and_show_series()
                else:
                    _play_series_node(node)

            def series_back() -> None:
                if not series_visible:
                    return
                if len(series_nav_stack) > 1:
                    series_nav_stack.pop()
                    render_and_show_series()
                else:
                    close_series_browser()

            def open_series_browser() -> None:
                nonlocal series_visible
                if not series_root_nodes:
                    player.show_text("No TV series found", duration_ms=3000)
                    return
                if not series_nav_stack:
                    series_nav_stack.append(_SeriesNavFrame(breadcrumb="TV Series", nodes=list(series_root_nodes)))
                if render_and_show_series():
                    series_visible = True
                    player.on_key_press("UP", lambda: move_series_selection(-1))
                    player.on_key_press("DOWN", lambda: move_series_selection(1))
                    player.on_key_press("PGUP", lambda: move_series_selection(-_SERIES_MAX_ROWS))
                    player.on_key_press("PGDWN", lambda: move_series_selection(_SERIES_MAX_ROWS))
                    player.on_key_press("ENTER", select_series_node)
                    player.on_key_press("KP_ENTER", select_series_node)
                    player.on_key_press("ESC", series_back)
                    player.on_key_press("LEFT", series_back)
                    logger.info("Series browser opened (%d root items)", len(series_root_nodes))

            def toggle_series_browser() -> None:
                if series_visible:
                    close_series_browser()
                    return
                if vod_visible:
                    close_vod_browser()
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
                open_series_browser()

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
                sync_base_up_down_bindings()
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
                if series_visible:
                    close_series_browser()
                if about_visible:
                    close_about_overlay()
                if history_browser_visible:
                    close_history_browser()
                open_schedule_browser()

            def toggle_guide() -> None:
                nonlocal guide_visible, guide_window_start, selected_channel_url, selected_channel_name, guide_filter, favorites_only
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
                if series_visible:
                    close_series_browser()
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

                visible = visible_guide_channels(
                    guide_channel_list(), epg, channel.url, max_rows=_GUIDE_MAX_ROWS, current_channel_name=channel.name
                )
                urls = [c.url for c in visible]
                selected = channel if channel.url in urls else (visible[0] if visible else None)
                selected_channel_url = selected.url if selected else None
                selected_channel_name = selected.name if selected else None

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
            player.on_key_press("i", _on_epg_info_key)
            player.on_resize(on_resize)  # keep the overlay correctly sized as the window is resized
            player.on_key_press("MOUSE_MOVE", on_mouse_move)  # trackpad/mouse activity reveals it too
            player.on_key_press("g", toggle_guide)  # press 'g' to toggle the full program guide
            player.on_key_press("b", switch_to_last_channel)  # 'b' (back) jumps to the previously watched channel
            player.on_key_press("h", toggle_favorite)  # 'h' (heart) favorites the playing/selected channel
            player.on_key_press("w", toggle_recordings_browser)  # 'w' (watch) browses past recordings
            player.on_key_press("u", toggle_schedule_browser)  # 'u' (upcoming) browses scheduled recordings
            player.on_key_press("m", toggle_vod_browser)  # 'm' (movies) browses VOD movies
            player.on_key_press("l", toggle_series_browser)  # 'l' (library) browses the Xtream TV series library
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
            player.on_key_press_or_hold("MENU", on_press=_on_epg_info_key, on_hold=toggle_guide)

        if plex_creds is not None:
            # Sibling to the "if channel is not None and display is not
            # None:" block above, not nested inside it -- a Plex session
            # has neither a channel nor an EPG display, so none of that
            # block's guide/VOD/recordings/schedule machinery or
            # keybindings are ever defined here. Auto-opened once,
            # immediately below, since a Plex-only launch has nothing
            # else on screen for the user to look at.

            if plex_theme_music:
                # A second, fully separate mpv instance (see
                # PlexThemePlayer's own docstring for why) -- constructed
                # once per Plex session, not per show, so switching shows
                # while browsing is just a fresh play() on the same
                # instance rather than spawning a new mpv process every
                # time.
                plex_theme_player = PlexThemePlayer()

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
                # Unconditional (not gated on plex_visible below) -- both
                # an explicit close and select_plex_node's "close before
                # playing" step (see this function's own docstring above)
                # need whatever theme is playing faded out, and this is
                # the one call site both paths already share.
                _fade_out_plex_theme()
                if not plex_visible:
                    return
                cancel_plex_image_refresh_timer()
                player.clear_overlay(overlay_id=_PLEX_OVERLAY_ID)
                for key in ("UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDWN", "ENTER", "KP_ENTER", "ESC", "/", "y"):
                    player.unbind_key(key)
                player.on_key_press("ENTER", toggle_live_pause)  # restore the base binding just removed above
                _teardown_plex_jump_bindings_if_active()
                rebind_plex_base_letter_keys()  # jump-nav (just torn down above, if it was active) shadowed these
                plex_visible = False
                sync_base_up_down_bindings()
                logger.info("Plex browser closed")

            def _start_plex_theme(node: PlexNode) -> None:
                # The debounce timer's own target -- fires once the
                # selection has sat still on `node` for
                # _PLEX_THEME_DEBOUNCE_SECONDS.
                nonlocal plex_theme_timer, plex_theme_current_rating_key, plex_theme_pending_key
                plex_theme_timer = None
                plex_theme_pending_key = None
                if plex_theme_player is None:
                    return
                cancel_plex_theme_fade_timer()
                plex_theme_current_rating_key = node.rating_key
                plex_theme_player.play(plex_theme_url(plex_creds, node.rating_key), _PLEX_THEME_VOLUME)

            def _fade_plex_theme_step(remaining_steps: int) -> None:
                nonlocal plex_theme_fade_timer, plex_theme_current_rating_key
                if plex_theme_player is None:
                    return
                if remaining_steps <= 0:
                    plex_theme_player.stop()
                    plex_theme_current_rating_key = None
                    plex_theme_fade_timer = None
                    return
                plex_theme_player.volume = _PLEX_THEME_VOLUME * (remaining_steps / _PLEX_THEME_FADE_STEPS)
                plex_theme_fade_timer = threading.Timer(
                    _PLEX_THEME_FADE_INTERVAL_SECONDS, _fade_plex_theme_step, args=(remaining_steps - 1,)
                )
                plex_theme_fade_timer.daemon = True
                plex_theme_fade_timer.start()

            def _fade_out_plex_theme() -> None:
                # Cancels rather than lets run to completion: switching
                # straight from one show's theme to another cuts instead
                # of true-crossfading (that would need two simultaneous
                # instances) -- see _update_plex_theme_music. This is for
                # the "leaving the browsing/show context entirely" case,
                # where an abrupt cut would be jarring.
                cancel_plex_theme_timer()
                cancel_plex_theme_fade_timer()
                if plex_theme_player is not None and plex_theme_current_rating_key is not None:
                    _fade_plex_theme_step(_PLEX_THEME_FADE_STEPS)

            def _update_plex_theme_music(node: PlexNode | None) -> None:
                # The single hook for every navigation path -- called from
                # render_and_show_plex right after it resolves
                # title_logo_node, since that already has exactly the
                # semantics wanted here: the nearest movie/show ancestor
                # in the nav stack, so a show's theme keeps playing
                # seamlessly while drilling from its poster into its own
                # season/episode list, not just while the poster itself
                # is the selected row (see _plex_title_logo_target).
                nonlocal plex_theme_timer, plex_theme_pending_key
                if plex_theme_player is None:
                    return
                target_key = None
                if node is not None and node.kind == "show" and not node.rating_key.startswith("series-title:"):
                    # The "series-title:" prefix marks a synthetic ancestor
                    # _plex_title_logo_target builds for a Continue-
                    # Watching on-deck episode with no real show frame in
                    # the nav stack (see its own docstring) -- that key
                    # isn't a real Plex id, so there's no real /theme
                    # endpoint to fetch for it.
                    target_key = node.rating_key

                if target_key is None:
                    # Nothing to play a theme for -- fade out whatever's
                    # playing, unless there's genuinely nothing playing, or
                    # a fade is already in progress (confirmed live: without
                    # that second check, a render_and_show_plex call that
                    # fires again for reasons unrelated to navigation while
                    # backed out of a show -- e.g. a background image-
                    # prefetch callback's own debounced re-render -- kept
                    # restarting an in-progress fade from full volume,
                    # indefinitely deferring the reset that lets that show's
                    # theme ever play again). Deliberately not folded into
                    # the "already at target" check below -- target_key is
                    # None here regardless of whether plex_theme_pending_key
                    # also happens to be None (nothing queued), and treating
                    # those as "the same None" would wrongly skip fading out
                    # a show that's actually still playing.
                    cancel_plex_theme_timer()
                    plex_theme_pending_key = None
                    if plex_theme_current_rating_key is not None and plex_theme_fade_timer is None:
                        _fade_out_plex_theme()
                    return

                if target_key == plex_theme_current_rating_key or target_key == plex_theme_pending_key:
                    # Already playing this exact show, or already queued
                    # to -- covers both "still on the same show" and
                    # holding an arrow key at a list boundary re-selecting
                    # the same row on every repeat, which would otherwise
                    # keep restarting the debounce timer forever.
                    return
                cancel_plex_theme_timer()
                plex_theme_pending_key = target_key
                plex_theme_timer = threading.Timer(_PLEX_THEME_DEBOUNCE_SECONDS, _start_plex_theme, args=(node,))
                plex_theme_timer.daemon = True
                plex_theme_timer.start()

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

            def _resolve_plex_title_logo_in_background(node: PlexNode) -> None:
                # Stage 1 of 2 for the full-screen backdrop's title logo:
                # resolve which TMDB image URL (if any) belongs to `node`
                # (see _plex_title_logo_target for how it's chosen).
                # Stage 2 -- actually fetching/decoding that image -- is
                # render_and_show_plex's own prefetch_images call, same as
                # every row thumbnail here; this only resolves the URL
                # string via a TMDB title/year search. No-ops if there's
                # no token configured, no title to search on, or this
                # node's rating_key is already cached (even as a resolved
                # `None`, i.e. "looked, TMDB had nothing") or in flight.
                if (
                    tmdb_api_token is None
                    or not node.title
                    or node.rating_key in plex_title_logo_urls
                    or node.rating_key in plex_title_logo_in_flight
                ):
                    return
                plex_title_logo_in_flight.add(node.rating_key)

                def _fetch() -> None:
                    try:
                        if node.kind == "movie":
                            url = fetch_movie_logo_cached(
                                node.title, node.year, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                            )
                        else:
                            url = fetch_tv_logo_cached(
                                node.title, node.year, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                            )
                        plex_title_logo_urls[node.rating_key] = url
                    finally:
                        plex_title_logo_in_flight.discard(node.rating_key)
                    # Redraw once the URL itself is known, purely to kick
                    # off stage 2 above (prefetch_images) for it -- without
                    # this, a URL landing after the browser's own most
                    # recent render would just sit unused until some
                    # unrelated redraw happened to come along. Reflects
                    # whatever's current at the time, same as every other
                    # redraw-on-fetch in this app -- harmless if the user's
                    # since navigated elsewhere.
                    if url and plex_visible:
                        render_and_show_plex()

                threading.Thread(target=_fetch, daemon=True).start()

            def plex_frame_nodes(frame: _PlexNavFrame) -> list[PlexNode]:
                # frame.nodes is always the full, unfiltered listing --
                # filtering happens here, live, at render/selection time
                # (mirrors guide_channel_list's favorites_only handling),
                # so toggling the filter never has to mutate or reconcile
                # two copies of a frame's node list.
                if not plex_favorites_only:
                    return frame.nodes
                return [n for n in frame.nodes if n.kind in _PLEX_FAVORITABLE_KINDS and n.rating_key in favorites]

            def _plex_frame_wants_jump_nav(frame: _PlexNavFrame) -> bool:
                # Jump-nav only makes sense for a listing of actual titles
                # (a library's movies or shows) -- not the library-root
                # section list (kind "library_movie"/"library_show"/
                # "continue_watching") and not a show's seasons or a
                # season's episodes, which are ordered, not named.
                nodes = plex_frame_nodes(frame)
                return bool(nodes) and nodes[0].kind in ("movie", "show")

            def jump_plex_selection(letter: str) -> None:
                # Mirrors jump_vod_selection -- see its own comment.
                if not plex_visible or not plex_nav_stack:
                    return
                frame = plex_nav_stack[-1]
                nodes = plex_frame_nodes(frame)
                target = jump_to_letter_index([n.title for n in nodes], frame.selected_index, letter)
                if target is not None:
                    frame.selected_index = target
                    render_and_show_plex()

            def _sync_plex_jump_bindings() -> None:
                # Called after every render where the top of plex_nav_stack
                # may have changed (open, drill in/back, search/year-filter
                # results) -- binds or unbinds the jump-nav keyset
                # (_PLEX_JUMP_NAV_CHARS -- a-z0-9 minus g/h/v/l/y, which
                # keep their own Plex actions live even at a movie/show
                # listing -- see _PLEX_JUMP_NAV_CHARS's own comment) to
                # match whether the *current* frame is a movie/show
                # listing, so letters fall through to their normal global
                # meaning (pause, favorite, grid view, ...) everywhere
                # else (the library root, seasons, episodes).
                nonlocal plex_jump_bindings_active
                if not plex_nav_stack:
                    return
                wants = _plex_frame_wants_jump_nav(plex_nav_stack[-1])
                if wants and not plex_jump_bindings_active:
                    for char in _PLEX_JUMP_NAV_CHARS:
                        player.on_key_press(char, lambda char=char: jump_plex_selection(char))
                    plex_jump_bindings_active = True
                elif not wants and plex_jump_bindings_active:
                    for char in _PLEX_JUMP_NAV_CHARS:
                        player.unbind_key(char)
                    rebind_plex_base_letter_keys()
                    plex_jump_bindings_active = False

            def _teardown_plex_jump_bindings_if_active() -> None:
                # The search/year-filter prompts and the item-menu popup
                # each take over the keyboard wholesale, but their own
                # bulk-unbind lists only cover the fixed Plex-mode letters
                # (h/v/g/l/... etc) -- not the rest of the jump-nav keyset,
                # which covers letters they don't know about (b, c, d, ...).
                # Called at the top of each of those before they bind their
                # own keys, so no stray jump-nav binding survives underneath
                # them; _sync_plex_jump_bindings() (called from their finish/
                # close counterparts) reinstates jump-nav afterward if the
                # frame underneath still wants it. Only _PLEX_JUMP_NAV_CHARS
                # (not the full _FILTER_INPUT_CHARS) -- g/h/v/l/y were never
                # jump-nav's to begin with, so unbinding them here would tear
                # out their real, always-live Plex action instead.
                nonlocal plex_jump_bindings_active
                if not plex_jump_bindings_active:
                    return
                for char in _PLEX_JUMP_NAV_CHARS:
                    player.unbind_key(char)
                plex_jump_bindings_active = False

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
                # The movie/show whose TMDB title logo belongs in the
                # backdrop's top-right corner, regardless of how deep
                # into its seasons/episodes the user's currently browsing
                # -- see _plex_title_logo_target's own docstring.
                title_logo_node = _plex_title_logo_target(plex_nav_stack, plex_frame_nodes)
                title_logo_url = None
                if title_logo_node is not None:
                    _resolve_plex_title_logo_in_background(title_logo_node)
                    title_logo_url = plex_title_logo_urls.get(title_logo_node.rating_key)
                _update_plex_theme_music(title_logo_node)
                # The synthetic "On Deck" listing (drilled in from the
                # continue_watching root row): an episode row shows its
                # season poster instead of the episode screengrab -- see
                # overlay.plex_row_thumb_url.
                on_deck = frame.source_kind == "continue_watching"
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
                        title_logo_url=title_logo_url,
                        on_deck=on_deck,
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
                        title_logo_url=title_logo_url,
                        on_deck=on_deck,
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
                prefetch_images(
                    (plex_row_thumb_url(node, on_deck=on_deck) for node in visible),
                    on_resolved=_on_plex_image_resolved,
                )
                # The selected episode's own season_thumb_url (see
                # overlay._plex_selected_poster) is never any visible
                # row's own thumb_url, so the prefetch above never
                # fetches it on its own -- without this, cached_image
                # would have nothing to return for it, ever (it's
                # cache-only/non-blocking, unlike fetch_image). (In the
                # On Deck listing the row prefetch above already covers
                # it for episodes, but the hero poster still wants it in
                # every other listing.)
                selected_node = nodes[frame.selected_index] if 0 <= frame.selected_index < len(nodes) else None
                if selected_node is not None and selected_node.season_thumb_url:
                    prefetch_images([selected_node.season_thumb_url], on_resolved=_on_plex_image_resolved)
                # Same non-blocking fetch/decode/redraw-on-resolve pipeline
                # as the thumbnails above, for the title logo URL (if any)
                # resolved by _resolve_plex_title_logo_in_background --
                # prefetch_images already no-ops on a falsy/already-cached/
                # in-flight URL, so this is harmless to call every render.
                if title_logo_url:
                    prefetch_images([title_logo_url], on_resolved=_on_plex_image_resolved)
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

                logger.info("%s: '%s'", action, node.title)
                # Unfavoriting the selected item while favorites-only is
                # active can shrink (or empty) the filtered list out from
                # under frame.selected_index -- clamp it back into bounds
                # for whatever the filtered view looks like now, same as
                # move_plex_selection already does on every move.
                remaining = plex_frame_nodes(frame)
                frame.selected_index = max(0, min(len(remaining) - 1, frame.selected_index)) if remaining else 0
                # Render *before* the message (mirrors toggle_plex_favorites_only)
                # -- a favorite toggle invalidates the whole grid tile cache
                # (see _plex_tile_signature), so this redraw can take long
                # enough in grid view that firing show_text first would let
                # a chunk of its short duration elapse before the heart
                # badge even reaches the screen.
                render_and_show_plex()
                player.show_text(f"{action}: {node.title}", duration_ms=1500)

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
                    logger.info("Resuming Plex item at %.0fs: %s", resume_at, redact_resource_url(item.url))
                else:
                    player.show_text(f"Playing: {item.title}", duration_ms=3000)
                    logger.info("Playing Plex item: %s", redact_resource_url(item.url))

            def _cancel_up_next() -> None:
                # Bound to ESC only while the prompt is showing (see
                # _start_up_next_countdown) -- cancels the countdown and
                # leaves playback exactly where it already is, same as
                # today with no next episode found at all. Also the
                # "hide" half of every other tick, not just an explicit
                # ESC press -- see _up_next_tick's own uses.
                nonlocal up_next_node, up_next_deadline, up_next_thumb, up_next_timer
                if up_next_timer is not None:
                    up_next_timer.cancel()
                    up_next_timer = None
                if up_next_node is not None:
                    player.clear_overlay(overlay_id=_UP_NEXT_OVERLAY_ID)
                    player.clear_overlay(overlay_id=_UP_NEXT_BACKDROP_OVERLAY_ID)
                    player.unbind_key("ESC")
                up_next_node = None
                up_next_deadline = None
                up_next_thumb = None

            def _up_next_tick() -> None:
                nonlocal up_next_timer
                if up_next_node is None or up_next_deadline is None:
                    return
                if _any_browser_open():
                    # Don't autoplay out from under the user while
                    # they're off doing something else -- same guard
                    # sync_base_up_down_bindings/the skip-marker poll
                    # loop already use.
                    _cancel_up_next()
                    return
                remaining = up_next_deadline - time.monotonic()
                if remaining <= 0:
                    node = up_next_node  # captured before _cancel_up_next clears it
                    _cancel_up_next()
                    _play_plex_node(node)
                    return
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_up_next_overlay(
                    up_next_node.title, up_next_node.subtitle, up_next_thumb, round(remaining), osd_size[0], osd_size[1]
                )
                edge_margin = round(osd_size[0] * 0.02)
                x = osd_size[0] - image.width - edge_margin
                y = osd_size[1] - image.height - edge_margin
                player.show_overlay(image, x=x, y=y, overlay_id=_UP_NEXT_OVERLAY_ID)
                up_next_timer = threading.Timer(_UP_NEXT_TICK_SECONDS, _up_next_tick)
                up_next_timer.daemon = True
                up_next_timer.start()

            def _start_up_next_countdown(node: PlexNode, thumb_image: Image.Image | None) -> None:
                # Only actually starts if nothing else currently owns the
                # screen/ESC -- the rare case of a browser still being
                # open right when the background find_next_episode lookup
                # (see handle_playback_ended) completes. No worse than
                # "no next episode found": playback just stays idle.
                nonlocal up_next_node, up_next_deadline, up_next_thumb
                if _any_browser_open():
                    return
                hide_skip_marker_prompt()  # a stale "Skip Credits" prompt from the episode that just ended shouldn't linger
                cancel_chapter_preview()  # ditto for a stale chapter preview -- see its own comment
                up_next_node = node
                up_next_thumb = thumb_image
                up_next_deadline = time.monotonic() + autoplay_countdown_seconds
                # Nothing's actually playing at this point (the previous
                # episode just ended) -- without this, mpv's own idle-
                # screen logo shows through behind the countdown card
                # instead of tvdinner's own background. Shown once, not
                # redrawn on every _up_next_tick like the card itself,
                # since it never changes for the life of the countdown.
                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                backdrop = render_up_next_backdrop(osd_size[0], osd_size[1])
                player.show_overlay(backdrop, x=0, y=0, overlay_id=_UP_NEXT_BACKDROP_OVERLAY_ID)
                player.on_key_press("ESC", _cancel_up_next)
                logger.info("Up Next: %s (%s)", node.title, node.subtitle or "no subtitle")
                _up_next_tick()

            def handle_playback_ended() -> None:
                # Fires only on a genuine natural end-of-file (see
                # Player.on_playback_ended's own docstring) -- never on a
                # channel/VOD switch or a manual quit, so playing_vod_item
                # here really is whatever just finished, not something
                # already replaced.
                item = playing_vod_item
                if not autoplay_next_episode or item is None or item.plex_parent_rating_key is None:
                    return

                def _lookup() -> None:
                    next_node = find_next_episode(
                        plex_creds,
                        item.rating_key,
                        item.plex_parent_rating_key,
                        item.plex_grandparent_rating_key,
                    )
                    if next_node is None:
                        return
                    thumb_image = fetch_image(next_node.thumb_url) if next_node.thumb_url else None
                    # Discard a stale result if the user has since started
                    # something else while this lookup was in flight --
                    # same `is` identity guard
                    # _enrich_vod_hero_art_in_background already uses.
                    if playing_vod_item is item:
                        _start_up_next_countdown(next_node, thumb_image)

                threading.Thread(target=_lookup, daemon=True).start()

            player.on_playback_ended(handle_playback_ended)

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
                    plex_nav_stack.append(
                        _PlexNavFrame(breadcrumb=breadcrumb, nodes=children, source_kind=node.kind)
                    )
                    render_and_show_plex()
                    _sync_plex_jump_bindings()
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

            def rebind_plex_base_letter_keys() -> None:
                # The always-on Plex-mode letter bindings -- restored
                # wherever something else (the item-menu popup, the
                # search/year-filter prompts, Plex browser jump-nav) shadows
                # all of a-z0-9 for its own use and needs to hand them back
                # afterward.
                player.on_key_press("z", cycle_aspect_ratio)
                player.on_key_press("e", cycle_sleep_timer)
                player.on_key_press("r", toggle_recording)
                player.on_key_press("p", toggle_live_pause)
                player.on_key_press("o", toggle_picture_in_picture)
                player.on_key_press("t", toggle_subtitles)
                player.on_key_press("a", toggle_about_overlay)
                player.on_key_press("l", toggle_plex_browser)
                player.on_key_press("i", _on_vod_info_key)
                player.on_key_press("k", toggle_chromecast_picker)
                player.on_key_press("x", toggle_history_browser)
                player.on_key_press("h", toggle_plex_favorite)
                player.on_key_press("v", toggle_plex_favorites_only)
                player.on_key_press("g", toggle_plex_grid_view)
                player.on_key_press("y", start_plex_year_input)

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
                rebind_plex_base_letter_keys()
                player.on_key_press("MENU", _on_vod_info_key)
                player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
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
                render_and_show_plex()
                _sync_plex_jump_bindings()

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
                _teardown_plex_jump_bindings_if_active()
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
                    _sync_plex_jump_bindings()
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
                    _sync_plex_jump_bindings()
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
                rebind_plex_base_letter_keys()
                player.on_key_press("MENU", _on_vod_info_key)
                player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
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
                render_and_show_plex()
                _sync_plex_jump_bindings()

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
                _sync_plex_jump_bindings()
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
                _teardown_plex_jump_bindings_if_active()
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
                rebind_plex_base_letter_keys()
                player.on_key_press("MENU", _on_vod_info_key)
                player.on_key_press("BS", stop_plex_playback_and_reopen_browser)
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
                render_and_show_plex()
                _sync_plex_jump_bindings()

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
                _teardown_plex_jump_bindings_if_active()
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
            player.on_key_press("i", _on_vod_info_key)  # 'i' shows info for whatever's currently playing
            # MENU has no guide to fall back to here (Plex has no live-
            # channel/EPG concept at all) -- unlike the channel session's
            # tap/hold split, it's simply a permanent alias for 'i'.
            player.on_key_press("MENU", _on_vod_info_key)
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
            cancel_sleep_timer()
            cancel_guide_logo_refresh_timer()
            cancel_history_image_refresh_timer()
            cancel_series_image_refresh_timer()
            cancel_live_pause_timer()
            cancel_reconnect_timer()
            cancel_reconnect_stability_timer()
            cancel_plex_theme_timer()
            cancel_plex_theme_fade_timer()
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
            skip_marker_stop_event.set()
            schedule_stop_event.set()
            watchlist_stop_event.set()
            watch_report_stop_event.set()
        except KeyboardInterrupt:
            logger.info("Interrupted again during shutdown -- finishing cleanup")
        finally:
            try:
                player.quit()
                if plex_theme_player is not None:
                    plex_theme_player.quit()
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
        "  tvdinner bookmarks list|add|edit|remove   manage bookmarks.json non-interactively\n"
        "  tvdinner default-handler         default opener for .m3u files + tvdinner:/tvtimes: links (Linux)\n"
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
        "--record-watchlist",
        action="store_true",
        help="For a tvtimes:// source, poll that account's watchlist every 15 minutes and "
        "schedule a recording for each upcoming airing anyone on it flagged -- set a reminder "
        "in the tvtimes web app (from your phone, say) and this box records it. Entries it "
        "creates are removed again when they leave the watchlist; recordings you scheduled by "
        "hand are never touched. Ignored for any other source",
    )
    parser.add_argument(
        "--report-watch-state",
        action="store_true",
        help="For a tvtimes:// source, report what you watch back to that account every 15 "
        "minutes, so its web guide can show watched programmes. Only live-channel watches "
        "from this tvtimes source are sent (never a local file, YouTube or Plex), as plain "
        "start/stop intervals -- tvtimes works out which programmes those cover. Ignored for "
        "any other source",
    )
    parser.add_argument(
        "--sync-favourites",
        action="store_true",
        help="For a tvtimes:// source, star the channels anyone on that account has favourited "
        "there. Additive and one-way, at startup: it never removes a favourite you set here, "
        "so un-starring in tvtimes leaves this box's star in place. Ignored for any other source",
    )
    parser.add_argument(
        "--device-name",
        metavar="NAME",
        help="Label this box in the watch state reported by --report-watch-state "
        "(e.g. 'living room'), so a household with more than one player can tell them apart",
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
        "--audio-passthrough",
        action="store_true",
        help="Send the encoded audio bitstream (AC3/DTS/E-AC3/TrueHD) straight to an AVR/"
        "soundbar over S/PDIF or HDMI instead of decoding it here -- only takes effect when the "
        "output device actually supports the format; mpv falls back to normal decoding "
        "otherwise, same as leaving this off.",
    )
    parser.add_argument(
        "--audio-downmix-boost",
        action="store_true",
        help="Raise the center/surround channels' volume when downmixing surround audio to "
        "stereo, so dialogue and surround effects don't end up quiet relative to the front L/R "
        "channels the way a naive downmix leaves them (mpv's own audio-normalize-downmix).",
    )
    parser.add_argument(
        "--loudness-normalization",
        action="store_true",
        help="Even out volume across (and between) titles via ffmpeg's loudnorm filter -- off "
        "by default, since it adds a small amount of processing and some listeners prefer a "
        "title's original dynamic range.",
    )
    parser.add_argument(
        "--no-chapter-skip",
        action="store_true",
        help="Keep UP/DOWN as mpv's default 60-second seek, even when playing a VOD item with "
        "real chapter markers (currently Plex only -- see the 'i' overlay's chapter ticks). On "
        "by default, UP/DOWN instead jump between chapters for such an item, and only fall back "
        "to the default seek when the item playing has none.",
    )
    parser.add_argument(
        "--no-skip-markers",
        action="store_true",
        help="Don't show the 'Skip Intro'/'Skip Credits' prompt (Plex only, and only for an item "
        "whose library has intro/credits detection -- a Plex Pass feature -- run on it). On by "
        "default; the prompt only ever seeks when you press 'j' to confirm it, never on its own.",
    )
    parser.add_argument(
        "--no-autoplay-next-episode",
        action="store_true",
        help="Don't offer the next episode of a Plex TV show when one finishes -- on by default, "
        "shown as an 'Up Next' prompt with a countdown once the episode actually ends (never mid-"
        "episode), cancelled with ESC.",
    )
    parser.add_argument(
        "--autoplay-countdown-seconds",
        type=float,
        default=10.0,
        metavar="SECONDS",
        help="How long the 'Up Next' prompt (see --no-autoplay-next-episode) waits before playing "
        "the next episode on its own (default: 10)",
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
        "--no-plex-theme-music",
        action="store_true",
        help="Don't play a Plex show's theme-music preview while browsing its library page "
        "(Plex source only) -- on by default, matching the official Plex clients. Starts after "
        "a short pause on a show, fades out on navigating away or picking something to actually "
        "watch",
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
        "--no-tmdb-cache",
        action="store_true",
        help="Always query TMDB instead of using a cached rating/metadata/artwork, and don't write "
        "one either -- same escape hatch as --no-epg-cache, for when a cached entry (e.g. a "
        "mismatched title) needs to stop being served without waiting out the "
        f"{DEFAULT_TMDB_CACHE_MAX_AGE.days}-day cache",
    )
    parser.add_argument(
        "--refresh-tmdb-cache",
        action="store_true",
        help="Force a fresh TMDB lookup for whatever's fetched this run, ignoring any existing "
        "cached entry no matter its age, then refresh the on-disk cache with it (unlike "
        "--no-tmdb-cache, later runs still benefit from the cache)",
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
    """Handle `tvdinner bookmarks [...]`.

    With no verb (or an unrecognised first token) this is the interactive
    picker (add/edit/delete/select) for saved playlist bookmarks --
    selecting one re-enters main() with that bookmark's url/epg/channel,
    exactly as if they'd been typed directly.

    `list` / `add` / `edit` / `remove` are non-interactive: they read and
    write bookmarks.json without opening the picker, for scripting and for
    other tools to manage the file (see run_bookmarks_list_command and
    friends). A bare numeric/free first token still falls through to the
    picker so nothing that worked before changes."""
    if argv[:1] == ["list"]:
        return run_bookmarks_list_command(argv[1:])
    if argv[:1] == ["add"]:
        return run_bookmarks_add_command(argv[1:])
    if argv[:1] == ["edit"]:
        return run_bookmarks_edit_command(argv[1:])
    if argv[:1] == ["remove"]:
        return run_bookmarks_remove_command(argv[1:])

    parser = argparse.ArgumentParser(
        prog="tvdinner bookmarks",
        description="Interactively manage and launch saved playlist bookmarks. "
        "The `list`, `add`, `edit` and `remove` verbs manage bookmarks.json "
        "non-interactively instead -- run `tvdinner bookmarks <verb> --help`.",
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
    selected, refresh_epg, tvtimes_full = result

    bookmark_argv = [selected.url]
    if selected.epg:
        bookmark_argv += ["--epg", selected.epg]
    if selected.channel:
        bookmark_argv += ["--channel", selected.channel]
    if selected.tmdb_api_token:
        bookmark_argv += ["--tmdb-api-token", selected.tmdb_api_token]
    if refresh_epg:
        bookmark_argv += ["--refresh-epg-cache"]
    if tvtimes_full:
        # The whole pairing in one go, matching the wiki's "everything at
        # once" -- the picker only offers this on a tvtimes:// row.
        bookmark_argv += ["--record-watchlist", "--report-watch-state", "--sync-favourites"]
        # Only ever the operator's own saved label -- never guessed from
        # the hostname, which would put a machine name into the account's
        # watch history that nobody asked to send.
        if selected.device_name:
            bookmark_argv += ["--device-name", selected.device_name]
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


def adopt_epg_shift_policy(display: EpgDisplay, epg: Epg | None) -> None:
    """Stop applying our clock shifts when the guide already carries them.

    tvtimes corrects times on export so nothing downstream has to. A
    --epg-shifts entry for the same channel -- which you still want when
    watching that channel direct from its provider -- would then apply the
    correction a second time, putting the guide a whole shift in the past.
    Suppressed rather than deleted, because a shift is keyed by channel name
    and the direct-from-provider case still needs it."""
    already = bool(epg and epg.times_already_corrected)
    if already and not display.guide_already_corrected:
        logger.info(
            "Guide generated by %r already carries clock corrections; "
            "local EPG shifts suppressed for it",
            epg.generator if epg else None,
        )
    display.guide_already_corrected = already


def _redact_source_url(url: str) -> str:
    """Mask credentials in any source URL before it reaches a log line or an
    error message: the xtream/stalker/plex login-scheme redactors, plus
    redact_resource_url for `user:pass@` userinfo, xtream stream-path creds and
    credential-ish query params (`?token=`, `?ticket=`, …). Non-credential URLs
    pass through unchanged."""
    return redact_resource_url(redact_plex_url(redact_stalker_url(redact_xtream_url(url))))


def _redact_bookmark_url(url: str) -> str:
    """Back-compat alias -- a bookmark's source URL is redacted the same way."""
    return _redact_source_url(url)


def _add_bookmarks_verb_args(parser: argparse.ArgumentParser) -> None:
    """--bookmarks-file / --log-file / --no-log, shared by every
    non-interactive `tvdinner bookmarks` verb (same three the interactive
    picker takes)."""
    parser.add_argument(
        "--bookmarks-file",
        metavar="PATH",
        help=f"JSON file storing bookmarks (default: {DEFAULT_BOOKMARKS_PATH})",
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log user actions and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--no-log", action="store_true", help="Disable file logging entirely")


def _bookmarks_verb_setup(args: argparse.Namespace) -> Path:
    """Configure logging from the shared flags and return the resolved
    bookmarks-file path."""
    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    return Path(args.bookmarks_file) if args.bookmarks_file else DEFAULT_BOOKMARKS_PATH


def _load_bookmarks_reporting_warnings(path: Path) -> list[Bookmark]:
    bookmarks, warnings = load_bookmarks(path)
    for warning in warnings:
        print(warning, file=sys.stderr)
        logger.warning("%s", warning)
    return bookmarks


def run_bookmarks_list_command(argv: list[str]) -> int:
    """`tvdinner bookmarks list [--json]`: print saved bookmarks. The
    table masks credentials in a bookmark's URL and never shows its TMDB
    token (same as the picker); `--json` emits the raw bookmarks.json
    array -- real URLs and tokens, the same bytes the caller could read
    from the file itself -- for a script to consume."""
    parser = argparse.ArgumentParser(
        prog="tvdinner bookmarks list",
        description="Print saved bookmarks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw bookmarks JSON array (real URLs and tokens) instead of a table",
    )
    _add_bookmarks_verb_args(parser)
    args = parser.parse_args(argv)
    path = _bookmarks_verb_setup(args)

    bookmarks = _load_bookmarks_reporting_warnings(path)

    if args.json:
        print(json.dumps([bookmark_to_dict(b) for b in bookmarks], indent=2))
        return 0

    if not bookmarks:
        print(f"No bookmarks saved ({path}).")
        return 0

    pad = " " * (len(str(len(bookmarks))) + 2)
    for position, bookmark in enumerate(bookmarks, start=1):
        print(f"{position:>{len(str(len(bookmarks)))}}. {bookmark.name}")
        print(f"{pad}url: {_redact_bookmark_url(bookmark.url)}")
        if bookmark.epg:
            print(f"{pad}epg: {bookmark.epg}")
        if bookmark.channel:
            print(f"{pad}channel: {bookmark.channel}")
        if bookmark.tmdb_api_token:
            print(f"{pad}tmdb-api-token: (set)")
        if bookmark.device_name:
            print(f"{pad}device-name: {bookmark.device_name}")
    return 0


def run_bookmarks_add_command(argv: list[str]) -> int:
    """`tvdinner bookmarks add --name ... --url ... [...]`: append a
    bookmark. Fails if the name is already taken unless `--replace`, which
    overwrites that row in place (keeping its position)."""
    parser = argparse.ArgumentParser(
        prog="tvdinner bookmarks add",
        description="Add a saved bookmark non-interactively.",
    )
    parser.add_argument("--name", required=True, help="Display name -- unique; the key `edit`/`remove` take")
    parser.add_argument(
        "--url",
        required=True,
        help="Anything the `url` positional accepts (M3U / Xtream / Stalker / HDHomeRun / Plex "
        "URL, a direct stream, or a local video file)",
    )
    parser.add_argument("--epg", help="XMLTV EPG URL (like --epg)")
    parser.add_argument("--channel", help="Default channel name or 1-based index (like -c/--channel)")
    parser.add_argument("--tmdb-api-token", help="Per-bookmark TMDB v4 read token (like --tmdb-api-token)")
    parser.add_argument(
        "--device-name",
        help="Label this box in the watch state reported to a tvtimes source (like --device-name)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="If a bookmark with this name exists, overwrite it in place instead of failing",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the stored row as JSON instead of a status line"
    )
    _add_bookmarks_verb_args(parser)
    args = parser.parse_args(argv)
    path = _bookmarks_verb_setup(args)

    bookmarks = _load_bookmarks_reporting_warnings(path)
    bookmark = Bookmark(
        name=args.name,
        url=strip_wrapping_quotes(args.url),
        epg=strip_wrapping_quotes(args.epg) if args.epg else None,
        channel=args.channel or None,
        tmdb_api_token=args.tmdb_api_token or None,
        device_name=args.device_name or None,
    )
    try:
        updated, replaced = upsert_bookmark(bookmarks, bookmark, replace=args.replace)
    except BookmarkError as exc:
        print(exc, file=sys.stderr)
        logger.error("bookmarks add: %s", exc)
        return 1

    save_bookmarks(path, updated)
    verb = "Replaced" if replaced else "Added"
    logger.info("%s bookmark %r (url=%s)", verb.lower(), bookmark.name, _redact_bookmark_url(bookmark.url))
    if args.json:
        print(json.dumps(bookmark_to_dict(bookmark), indent=2))
    else:
        print(f"{verb} bookmark {bookmark.name!r} ({path}).")
    return 0


def run_bookmarks_edit_command(argv: list[str]) -> int:
    """`tvdinner bookmarks edit <NAME|INDEX> [...]`: change fields on an
    existing bookmark. Unspecified fields keep their value; `--clear-epg`
    / `--clear-channel` / `--clear-tmdb-api-token` / `--clear-device-name`
    unset an optional one."""
    parser = argparse.ArgumentParser(
        prog="tvdinner bookmarks edit",
        description="Change fields on an existing bookmark.",
    )
    parser.add_argument(
        "bookmark",
        metavar="NAME|INDEX",
        help="Which bookmark: an exact name, or a 1-based position from `bookmarks list`",
    )
    parser.add_argument("--name", help="New display name")
    parser.add_argument("--url", help="New source URL")
    epg = parser.add_mutually_exclusive_group()
    epg.add_argument("--epg", help="Set the XMLTV EPG URL")
    epg.add_argument("--clear-epg", action="store_true", help="Remove the stored EPG URL")
    channel = parser.add_mutually_exclusive_group()
    channel.add_argument("--channel", help="Set the default channel")
    channel.add_argument("--clear-channel", action="store_true", help="Remove the stored default channel")
    tmdb = parser.add_mutually_exclusive_group()
    tmdb.add_argument("--tmdb-api-token", help="Set the per-bookmark TMDB token")
    tmdb.add_argument(
        "--clear-tmdb-api-token", action="store_true", help="Remove the per-bookmark TMDB token"
    )
    device = parser.add_mutually_exclusive_group()
    device.add_argument("--device-name", help="Set the per-bookmark tvtimes device name")
    device.add_argument(
        "--clear-device-name", action="store_true", help="Remove the per-bookmark tvtimes device name"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the updated row as JSON instead of a status line"
    )
    _add_bookmarks_verb_args(parser)
    args = parser.parse_args(argv)
    path = _bookmarks_verb_setup(args)

    changes = (
        args.name is not None
        or args.url is not None
        or args.epg is not None
        or args.clear_epg
        or args.channel is not None
        or args.clear_channel
        or args.tmdb_api_token is not None
        or args.clear_tmdb_api_token
        or args.device_name is not None
        or args.clear_device_name
    )
    if not changes:
        print("Nothing to change -- pass at least one field to set or clear.", file=sys.stderr)
        return 1

    bookmarks = _load_bookmarks_reporting_warnings(path)
    found = find_bookmark(bookmarks, args.bookmark)
    if found is None:
        print(f"No bookmark matches {args.bookmark!r}.", file=sys.stderr)
        logger.error("bookmarks edit: no match for %r", args.bookmark)
        return 1
    index, current = found

    if args.name is not None and args.name != current.name:
        other = find_bookmark(bookmarks, args.name)
        if not args.name.isdigit() and other is not None and other[0] != index:
            print(f"A bookmark named {args.name!r} already exists.", file=sys.stderr)
            logger.error("bookmarks edit: rename to %r would clash", args.name)
            return 1

    edited = Bookmark(
        name=args.name if args.name is not None else current.name,
        url=strip_wrapping_quotes(args.url) if args.url is not None else current.url,
        epg=None if args.clear_epg else (strip_wrapping_quotes(args.epg) if args.epg is not None else current.epg),
        channel=None if args.clear_channel else (args.channel if args.channel is not None else current.channel),
        tmdb_api_token=None
        if args.clear_tmdb_api_token
        else (args.tmdb_api_token if args.tmdb_api_token is not None else current.tmdb_api_token),
        device_name=None
        if args.clear_device_name
        else (args.device_name if args.device_name is not None else current.device_name),
    )
    updated = list(bookmarks)
    updated[index] = edited
    save_bookmarks(path, updated)
    logger.info("edited bookmark %r (url=%s)", edited.name, _redact_bookmark_url(edited.url))
    if args.json:
        print(json.dumps(bookmark_to_dict(edited), indent=2))
    else:
        print(f"Updated bookmark {edited.name!r} ({path}).")
    return 0


def run_bookmarks_remove_command(argv: list[str]) -> int:
    """`tvdinner bookmarks remove <NAME|INDEX>`: delete a saved bookmark."""
    parser = argparse.ArgumentParser(
        prog="tvdinner bookmarks remove",
        description="Delete a saved bookmark.",
    )
    parser.add_argument(
        "bookmark",
        metavar="NAME|INDEX",
        help="Which bookmark: an exact name, or a 1-based position from `bookmarks list`",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the removed row as JSON instead of a status line"
    )
    _add_bookmarks_verb_args(parser)
    args = parser.parse_args(argv)
    path = _bookmarks_verb_setup(args)

    bookmarks = _load_bookmarks_reporting_warnings(path)
    try:
        updated, removed = remove_bookmark(bookmarks, args.bookmark)
    except BookmarkError as exc:
        print(f"{exc}.", file=sys.stderr)
        logger.error("bookmarks remove: %s", exc)
        return 1

    save_bookmarks(path, updated)
    logger.info("removed bookmark %r", removed.name)
    if args.json:
        print(json.dumps(bookmark_to_dict(removed), indent=2))
    else:
        print(f"Removed bookmark {removed.name!r} ({path}).")
    return 0


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


def _format_stats_duration(seconds: float) -> str:
    """Same "12h 34m" / "45m" / "30s" shape as overlay.py's own
    _format_history_duration (used for a single watch's length in the
    'x' browser) -- duplicated rather than imported, same reasoning as
    _format_cache_bytes above."""
    total_seconds = round(seconds)
    if total_seconds < 60:
        return f"{total_seconds}s"
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _period_starts(now: datetime) -> dict[str, datetime | None]:
    """Local-calendar lower bounds for each watching-activity reporting
    period, as of `now` (already localized) -- None for "All time" (no
    lower bound). Takes `now` as a plain parameter rather than reading
    the clock itself, so it's directly unit-testable with a fixed value."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = midnight - timedelta(days=now.weekday())
    month_start = midnight.replace(day=1)
    return {"This week": week_start, "This month": month_start, "All time": None}


def _watch_seconds_by_kind(entries: list[HistoryEntry], since: datetime | None) -> dict[HistoryKind, float]:
    """Total duration_seconds per kind, for entries whose started_at
    (converted to local time) falls on or after `since` -- every entry,
    if `since` is None. A watch is bucketed by when it started."""
    totals: dict[HistoryKind, float] = {"channel": 0.0, "vod": 0.0, "recording": 0.0}
    for entry in entries:
        if since is not None and entry.started_at.astimezone() < since:
            continue
        totals[entry.kind] += entry.duration_seconds
    return totals


def _top_channels(entries: list[HistoryEntry], since: datetime | None, limit: int = 5) -> list[tuple[str, float]]:
    """The `limit` most-watched live channels (by total duration_seconds)
    since `since` (every "channel"-kind entry, if None), descending.
    Grouped by channel_name, falling back to title for the rare entry
    missing one -- same defensive fallback overlay.py's own history rows
    already use for this field."""
    totals: dict[str, float] = {}
    for entry in entries:
        if entry.kind != "channel":
            continue
        if since is not None and entry.started_at.astimezone() < since:
            continue
        name = entry.channel_name or entry.title
        totals[name] = totals.get(name, 0.0) + entry.duration_seconds
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]


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
    subscription, Plex) is listed as unknown rather than guessed.

    Also reports watching activity from the history log itself (see
    history.py) -- total watch time this week/month/all-time, split by
    channel/VOD/recording, plus the most-watched live channels this
    month and all-time. Purely a read of already-recorded data; nothing
    here is fetched over the network or newly tracked."""
    parser = argparse.ArgumentParser(
        prog="tvdinner stats",
        description="Show on-disk cache usage (per bookmarked feed's EPG cache where knowable, plus the "
        "TMDB/image/online-logo caches every feed shares) and watching activity by week/month/all-time.",
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
        if not epg_source and is_tvtimes_url(bookmark.url):
            feed = parse_tvtimes_url(bookmark.url)
            if feed is not None:
                epg_source = tvtimes_epg_url(feed)
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

    history_entries, history_content_warnings = load_history(history_path)
    for warning in history_content_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)

    print("\nWatching activity:\n")
    if not history_entries:
        print("No watch history recorded yet.")
    else:
        now = datetime.now().astimezone()
        periods = _period_starts(now)
        period_rows = []
        for label, since in periods.items():
            totals = _watch_seconds_by_kind(history_entries, since)
            period_rows.append(
                [
                    label,
                    _format_stats_duration(totals["channel"]),
                    _format_stats_duration(totals["vod"]),
                    _format_stats_duration(totals["recording"]),
                    _format_stats_duration(sum(totals.values())),
                ]
            )
        _print_stats_table(["Period", "Channel", "VOD", "Recording", "Total"], period_rows, right_align={1, 2, 3, 4})

        # Only worth showing at all if there's any live-channel watching in
        # the log -- a Plex-only or VOD-only user would otherwise just see
        # two empty "None yet." tables.
        if any(entry.kind == "channel" for entry in history_entries):
            for label, since in (("This month", periods["This month"]), ("All time", None)):
                top = _top_channels(history_entries, since)
                print(f"\nTop channels ({label.lower()}):\n")
                if top:
                    _print_stats_table(
                        ["Channel", "Time"],
                        [[name, _format_stats_duration(seconds)] for name, seconds in top],
                        right_align={1},
                    )
                else:
                    print("None yet.")

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
    playback_positions_file = (
        Path(args.playback_positions_file) if args.playback_positions_file else DEFAULT_PLAYBACK_POSITIONS_PATH
    )
    files: list[tuple[str, Path]] = [
        ("Bookmarks", config_paths["bookmarks.json"]),
        ("Favorites", config_paths["favorites.json"]),
        ("EPG shifts", config_paths["epg_shifts.json"]),
        ("Stored default TMDB token", config_paths["tmdb_token.json"]),
        (
            "Scheduled recordings",
            Path(args.schedule_file) if args.schedule_file else DEFAULT_SCHEDULE_PATH,
        ),
        ("Playback positions", playback_positions_file),
        # Sibling of the file above (see
        # playback_positions.playback_position_timestamps_path_for) --
        # tracks when each VOD resume position was last touched, for its
        # own age-based pruning. Not user-facing config, but a real file
        # this app writes, so hard-reset needs to remove it too.
        ("Playback position timestamps", playback_position_timestamps_path_for(playback_positions_file)),
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


# What tvdinner's shipped desktop entry claims: the .m3u/.m3u8 MIME types,
# the tvdinner: URL scheme (a tvtimes "Play" link, say), and the
# tvtimes(s): one -- tvtimes' own "Open in tvdinner" button, which hands
# over a whole account's export feeds rather than one channel. Keep in sync
# with `MimeType=` in data/tvdinner.desktop.
_HANDLED_TYPES = (
    "audio/x-mpegurl",
    "audio/mpegurl",
    "application/x-mpegurl",
    "application/vnd.apple.mpegurl",
    "x-scheme-handler/tvdinner",
    "x-scheme-handler/tvtimes",
    "x-scheme-handler/tvtimess",
)

# Written only when no packaged/user tvdinner.desktop is found (a
# from-source install). Mirrors data/tvdinner.desktop, which the .deb /
# .rpm install to /usr/share/applications/.
_DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=tvdinner
GenericName=IPTV Player
Comment=Play an M3U playlist with an on-screen EPG overlay
Exec=tvdinner %u
Icon=tvdinner
Terminal=true
Categories=AudioVideo;Video;Player;TV;
MimeType=audio/x-mpegurl;audio/mpegurl;application/x-mpegurl;application/vnd.apple.mpegurl;x-scheme-handler/tvdinner;x-scheme-handler/tvtimes;x-scheme-handler/tvtimess;
Keywords=iptv;m3u;m3u8;playlist;epg;xtream;stalker;
"""


def _xdg_data_dirs() -> list[Path]:
    """XDG_DATA_HOME then XDG_DATA_DIRS, with the spec defaults."""
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    rest = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(home), *(Path(p) for p in rest.split(":") if p)]


def _find_desktop_file(desktop_id: str) -> Path | None:
    for root in _xdg_data_dirs():
        candidate = root / "applications" / desktop_id
        if candidate.is_file():
            return candidate
    return None


def run_default_handler_command(argv: list[str]) -> int:
    """Handle `tvdinner default-handler`: make tvdinner this user's default
    opener for `.m3u` / `.m3u8` files *and* for `tvdinner:` links (e.g. a
    tvtimes "Play" button), so opening one launches tvdinner with no
    application-chooser dialog. Linux only -- it just runs `xdg-mime
    default`, which writes the user's own `~/.config/mimeapps.list`; no
    root, nothing system-wide. Undo it from a file manager's "Open With"
    dialog, or by editing that file."""
    parser = argparse.ArgumentParser(
        prog="tvdinner default-handler",
        description=(
            "Set tvdinner as this user's default opener for .m3u / .m3u8 files and "
            "tvdinner: / tvtimes: links (Linux)."
        ),
    )
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        help=f"Where to log user actions and warnings/errors (default: {DEFAULT_LOG_PATH})",
    )
    parser.add_argument("--no-log", action="store_true", help="Disable file logging entirely")
    args = parser.parse_args(argv)
    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    logger.info("Starting tvdinner %s default-handler", __version__)

    if sys.platform == "win32":
        print(
            "default-handler is Linux only -- on Windows the installer does this for you.\n"
            "tvdinner: / tvtimes: links are registered automatically when you install; if a\n"
            "link does nothing, re-run the installer (a version before 1.41 didn't register\n"
            "them). For .m3u files, tick 'Open .m3u / .m3u8 playlists with tvdinner' during\n"
            "install, or right-click a .m3u -> Open with -> Choose another app -> tvdinner\n"
            "-> Always.",
            file=sys.stderr,
        )
        return 1
    if sys.platform == "darwin":
        print(
            "default-handler is Linux only. On macOS, right-click a .m3u -> Get Info ->\n"
            "Open with -> tvdinner -> Change All.",
            file=sys.stderr,
        )
        return 1

    xdg_mime = shutil.which("xdg-mime")
    if xdg_mime is None:
        print(
            "xdg-mime not found -- install xdg-utils (Debian/Ubuntu: sudo apt install xdg-utils;\n"
            "Fedora: sudo dnf install xdg-utils), then re-run.",
            file=sys.stderr,
        )
        logger.error("default-handler: xdg-mime not on PATH")
        return 1

    desktop_id = "tvdinner.desktop"
    if _find_desktop_file(desktop_id) is None:
        # from-source install with no package: drop a user-level entry so
        # the association resolves to something runnable.
        apps_dir = _xdg_data_dirs()[0] / "applications"
        dest = apps_dir / desktop_id
        try:
            apps_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(_DESKTOP_ENTRY)
        except OSError as exc:
            print(f"Could not write {dest}: {exc}", file=sys.stderr)
            logger.error("default-handler: writing %s failed: %s", dest, exc)
            return 1
        print(f"No installed desktop entry found; wrote one to {dest}.")
        logger.info("default-handler: wrote %s", dest)
        update_db = shutil.which("update-desktop-database")
        if update_db is not None:
            subprocess.run([update_db, str(apps_dir)], check=False, capture_output=True)

    # `xdg-mime default` (generic path) writes $XDG_CONFIG_HOME/mimeapps.list
    # but doesn't create the directory -- if it's missing the write fails and
    # the type is silently skipped. Normally ~/.config exists; make sure.
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    try:
        config_home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Could not create {config_home}: {exc}", file=sys.stderr)
        logger.error("default-handler: mkdir %s failed: %s", config_home, exc)
        return 1

    try:
        subprocess.run(
            [xdg_mime, "default", desktop_id, *_HANDLED_TYPES],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        print(f"xdg-mime failed: {detail or exc}", file=sys.stderr)
        logger.error("default-handler: xdg-mime default failed: %s", detail or exc)
        return 1

    all_set = True
    for mime in _HANDLED_TYPES:
        current = subprocess.run(
            [xdg_mime, "query", "default", mime],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        note = "ok" if current == desktop_id else f"still {current or '(none)'}"
        print(f"  {mime:<32}  {note}")
        all_set = all_set and current == desktop_id

    if not all_set:
        print(
            "\nSome types didn't take -- a desktop environment sometimes ships its own\n"
            "mimeapps.list that wins. Set it from your file manager's Open With dialog instead.",
            file=sys.stderr,
        )
        logger.warning("default-handler: verification incomplete")
        return 1

    print(
        "\ntvdinner is now the default for .m3u / .m3u8 and for tvdinner: /\n"
        "tvtimes: links.\n"
        "Double-click an .m3u to test."
    )
    print(
        "Note: a browser keeps its own per-download-type setting -- the first .m3u you\n"
        "download may still prompt once (tick \"open with tvdinner\" and \"always\"). A\n"
        "tvdinner: link (tvtimes' Play button) or a tvtimes: one (its Open in tvdinner\n"
        "button) skips the download entirely."
    )
    logger.info("default-handler: set %s as default for %s", desktop_id, ", ".join(_HANDLED_TYPES))
    return 0


def _normalize_launch_url(url: str) -> str:
    """Undo what a desktop launcher can wrap around the positional URL:

    - a ``tvdinner:`` scheme prefix, from an ``x-scheme-handler/tvdinner``
      link (e.g. a tvtimes "Play" button). **Only an http(s) payload is
      unwrapped** -- ``tvdinner:https://host/x.m3u`` -> ``https://host/x.m3u``.
      A ``tvdinner:`` link must never be able to smuggle in a local path or an
      mpv ``edl://`` / ``av://lavfi:`` protocol that would read local files, so
      anything else keeps its (inert) ``tvdinner:`` prefix and simply fails to
      open.
    - a ``file://`` URI, from ``Exec=… %u`` opening a local file (never carries
      a ``tvdinner:`` prefix -- it comes straight from the file manager) --
      ``file:///home/me/My%20List.m3u`` -> ``/home/me/My List.m3u``.

    Anything else is returned unchanged."""
    match = re.match(r"tvdinner:(?://)?(https?://.+)$", url, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    if url.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(url).path)
    return url


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
    if raw_argv[:1] == ["default-handler"]:
        return run_default_handler_command(raw_argv[1:])

    args = build_parser().parse_args(argv)
    # A copy-pasted example URL (this project's own docs show them shell-
    # quoted, e.g. tvdinner 'hdhomerun://192.168.1.50') can end up with
    # literal quote characters baked in if pasted somewhere that isn't a
    # shell -- a saved bookmark, or a launcher/script that doesn't do
    # shell-style quote removal. Strip a single matching pair here so
    # that mistake doesn't silently break scheme detection.
    args.url = strip_wrapping_quotes(args.url)
    # A desktop launcher can hand us a `tvdinner:` scheme URL (from an
    # x-scheme-handler link) or a `file://` URI (Exec=… %u on a local
    # file) -- unwrap either to the plain URL/path the rest of main()
    # expects.
    args.url = _normalize_launch_url(args.url)
    if args.epg:
        args.epg = strip_wrapping_quotes(args.epg)

    log_path = None if args.no_log else (Path(args.log_file) if args.log_file else DEFAULT_LOG_PATH)
    configure_logging(log_path)
    logger.info(
        "Starting tvdinner %s (playlist=%s, epg=%s, channel=%s)",
        __version__,
        _redact_source_url(args.url),
        args.epg,
        args.channel,
    )

    # A tvtimes:// URL is sugar for that server's two export feeds, not a
    # protocol of its own -- expand it here, before the source dispatch
    # below, so the rest of main() sees the ordinary M3U playlist URL it
    # already knows how to handle (see tvdinner.tvtimes).
    tvtimes_feed: TvtimesFeed | None = None
    if is_tvtimes_url(args.url):
        feed = parse_tvtimes_url(args.url)
        if feed is None:
            print(
                "Invalid tvtimes:// URL: expected tvtimes://host[:port]?token=... "
                "(tvtimess:// for https)",
                file=sys.stderr,
            )
            logger.error("Invalid tvtimes:// URL: %s", _redact_source_url(args.url))
            return 1
        args.url = tvtimes_playlist_url(feed)
        # The playlist's own `url-tvg=` header would work too, but tvtimes
        # builds that from its configured public origin -- which need not be
        # the address this machine reaches it on. An explicit --epg still wins.
        if not args.epg:
            args.epg = tvtimes_epg_url(feed)
        tvtimes_feed = feed
        logger.info("tvtimes source at %s", feed.base_url)

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
    # A raw Xtream/Stalker login URL carries a real password/mac -- never
    # used as the favorites.json key directly (a Plex session already
    # avoids this the same way, keying off plex_creds.base_url instead --
    # see below). stable_credential_key leaves any other source's url
    # (M3U, HDHomeRun, a local file) completely unchanged.
    favorites_feed_key = stable_credential_key(args.url)
    favorites, favorites_warnings = load_favorites(favorites_path, favorites_feed_key)
    for warning in favorites_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
        logger.warning(warning)
    if not favorites and favorites_feed_key != args.url:
        # A favorites.json saved before this fix existed is still keyed
        # by the raw, credential-bearing args.url -- migrate it onto the
        # safe key above (and scrub the old entry, not just leave an
        # empty one sitting next to it) rather than silently losing it.
        legacy_favorites, legacy_warnings = load_favorites(favorites_path, args.url)
        favorites_warnings += legacy_warnings
        if legacy_favorites:
            favorites = legacy_favorites
            try:
                save_favorites(favorites_path, favorites_feed_key, favorites)
                remove_favorites_feed(favorites_path, args.url)
                logger.info("Migrated favorites for this feed off the legacy, credential-bearing key")
            except OSError as exc:
                logger.warning("Could not migrate favorites to the new key: %s", exc)

    if args.sync_favourites and tvtimes_feed is not None:
        # Additive on purpose: stars set in the tvtimes web app appear here,
        # but un-starring there never removes one you set locally. tvdinner's
        # favorites.json records only names, with no note of where each came
        # from, so a two-way reconcile can't tell "removed upstream" from
        # "added here" -- and quietly deleting someone's own favorite is the
        # worse failure. Documented as one-way in the README.
        remote_favourites, favourites_error = fetch_tvtimes_favourites(tvtimes_feed)
        if favourites_error:
            print(f"Warning: {favourites_error}", file=sys.stderr)
            logger.warning("%s", favourites_error)
        else:
            added = remote_favourites - favorites
            if added:
                favorites |= added
                try:
                    save_favorites(favorites_path, favorites_feed_key, favorites)
                    logger.info("Synced %d favourite(s) from tvtimes", len(added))
                except OSError as exc:
                    logger.warning("Could not save favourites synced from tvtimes: %s", exc)

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
    # Same --no-X-cache/--refresh-X-cache shape as epg_cache_dir/epg_max_age
    # above -- --no-tmdb-cache never reads or writes a cache entry at all,
    # while --refresh-tmdb-cache makes any existing entry look expired (so
    # every fetch below hits the network) but still writes the result back,
    # so later runs still benefit.
    tmdb_cache_dir = None if args.no_tmdb_cache else DEFAULT_TMDB_CACHE_DIR
    tmdb_cache_max_age = timedelta(0) if args.refresh_tmdb_cache else DEFAULT_TMDB_CACHE_MAX_AGE

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
    series_root_nodes: list[SeriesNode] = []
    xtream_creds: XtreamCreds | None = None

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
        xtream_creds = creds
        series_root_nodes, series_error = list_xtream_series_children(creds, None)
        if series_error:
            logger.warning("Could not load Xtream series library (continuing without it): %s", series_error)
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
            plex_theme_music=not args.no_plex_theme_music,
            record_dir=record_dir,
            live_buffer_minutes=args.live_buffer_minutes,
            playback_positions=playback_positions,
            playback_positions_path=playback_positions_path,
            update_checker=update_checker,
            full_screen=not args.disable_full_screen,
            glsl_shader=args.glsl_shader,
            interpolation=args.interpolation,
            audio_passthrough=args.audio_passthrough,
            audio_downmix_boost=args.audio_downmix_boost,
            loudness_normalization=args.loudness_normalization,
            chapter_skip=not args.no_chapter_skip,
            skip_markers=not args.no_skip_markers,
            autoplay_next_episode=not args.no_autoplay_next_episode,
            autoplay_countdown_seconds=args.autoplay_countdown_seconds,
            playlist_source=plex_creds.base_url,
            history_path=history_path,
            favorites=plex_favorites,
            favorites_path=favorites_path,
            favorites_feed=plex_creds.base_url,
            tmdb_api_token=tmdb_api_token,
            tmdb_cache_dir=tmdb_cache_dir,
            tmdb_cache_max_age=tmdb_cache_max_age,
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
                    metadata = fetch_movie_metadata_cached(candidate, year, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age)
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
                    tmdb_id=metadata.tmdb_id,
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
            audio_passthrough=args.audio_passthrough,
            audio_downmix_boost=args.audio_downmix_boost,
            loudness_normalization=args.loudness_normalization,
            chapter_skip=not args.no_chapter_skip,
            skip_markers=not args.no_skip_markers,
            autoplay_next_episode=not args.no_autoplay_next_episode,
            autoplay_countdown_seconds=args.autoplay_countdown_seconds,
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
                    metadata = fetch_movie_metadata_cached(
                        candidate, lookup_year, tmdb_api_token, tmdb_cache_dir, tmdb_cache_max_age
                    )
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
                        tmdb_id=metadata.tmdb_id,
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
            audio_passthrough=args.audio_passthrough,
            audio_downmix_boost=args.audio_downmix_boost,
            loudness_normalization=args.loudness_normalization,
            chapter_skip=not args.no_chapter_skip,
            skip_markers=not args.no_skip_markers,
            autoplay_next_episode=not args.no_autoplay_next_episode,
            autoplay_countdown_seconds=args.autoplay_countdown_seconds,
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
                _redact_source_url(args.url),
            )
            return play_stream(
                args.url,
                record_dir=record_dir,
                live_buffer_minutes=args.live_buffer_minutes,
                update_checker=update_checker,
                full_screen=not args.disable_full_screen,
                glsl_shader=args.glsl_shader,
                interpolation=args.interpolation,
                audio_passthrough=args.audio_passthrough,
                audio_downmix_boost=args.audio_downmix_boost,
                loudness_normalization=args.loudness_normalization,
                chapter_skip=not args.no_chapter_skip,
                skip_markers=not args.no_skip_markers,
                autoplay_next_episode=not args.no_autoplay_next_episode,
                autoplay_countdown_seconds=args.autoplay_countdown_seconds,
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
        adopt_epg_shift_policy(display, epg)
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
        xtream_creds=xtream_creds,
        series_root_nodes=series_root_nodes,
        epg_loader=epg_loader,
        online_logos_loader=online_logos_loader,
        tmdb_api_token=tmdb_api_token,
        tmdb_cache_dir=tmdb_cache_dir,
        tmdb_cache_max_age=tmdb_cache_max_age,
        display=display,
        epg_shifts_path=epg_shifts_path,
        favorites=favorites,
        favorites_path=favorites_path,
        favorites_feed=favorites_feed_key,
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
        audio_passthrough=args.audio_passthrough,
        audio_downmix_boost=args.audio_downmix_boost,
        loudness_normalization=args.loudness_normalization,
        chapter_skip=not args.no_chapter_skip,
        skip_markers=not args.no_skip_markers,
        autoplay_next_episode=not args.no_autoplay_next_episode,
        autoplay_countdown_seconds=args.autoplay_countdown_seconds,
        playlist_source=_redact_source_url(args.url),
        history_path=history_path,
        tvtimes_watchlist_feed=tvtimes_feed if args.record_watchlist else None,
        tvtimes_watch_report_feed=tvtimes_feed if args.report_watch_state else None,
        tvtimes_device_name=strip_wrapping_quotes(args.device_name) if args.device_name else None,
        tvtimes_web_feed=tvtimes_feed,
    )


if __name__ == "__main__":
    sys.exit(main())
