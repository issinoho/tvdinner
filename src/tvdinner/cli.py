"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tvdinner import __version__
from tvdinner.epg import (
    DEFAULT_CHANNEL_SHIFTS_PATH,
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
from tvdinner.m3u import Channel, load_playlist
from tvdinner.overlay import (
    fetch_image,
    guide_eligible_channels,
    guide_reference_time,
    render_epg_overlay,
    render_guide_filter_prompt,
    render_program_guide,
    render_programme_details,
    selected_guide_programme,
    visible_guide_channels,
)
from tvdinner.player import Player, StreamInfo

_OVERLAY_TOP_MARGIN = 40
_GUIDE_BOTTOM_MARGIN = 40
_OVERLAY_HIDE_AFTER_SECONDS = 6.0
_OVERLAY_RESIZE_DEBOUNCE_SECONDS = 0.2
_OVERLAY_MOUSE_MOVE_THROTTLE_SECONDS = 1.0
_GUIDE_OVERLAY_ID = 1
_DETAILS_OVERLAY_ID = 2
_FILTER_OVERLAY_ID = 3
_GUIDE_TIME_STEP = timedelta(minutes=30)
_SHIFT_NUDGE_STEP = timedelta(minutes=1)
_GUIDE_MAX_ROWS = 8  # kept in sync with render_and_show_guide's max_rows so a page = a full screen
# Keys with no meaning outside the guide; suspended while typing a filter
# query too, since they have no character-input equivalent to shadow them.
_GUIDE_NAV_ONLY_KEYS = ("LEFT", "RIGHT", "UP", "DOWN", "PGUP", "PGDWN", "[", "]")
_FILTER_INPUT_CHARS = list("abcdefghijklmnopqrstuvwxyz0123456789")
_DEFAULT_CANVAS_WIDTH = 1920
_DEFAULT_CANVAS_HEIGHT = 1080
_OSD_SIZE_WAIT_SECONDS = 2.0
_OSD_SIZE_POLL_INTERVAL = 0.05

# None = automatic (the container/stream's own aspect ratio); cycled with 'z'.
_ASPECT_RATIOS: list[tuple[str | None, str]] = [
    (None, "Auto"),
    ("4:3", "4:3"),
    ("16:9", "16:9"),
    ("2.35:1", "2.35:1 (Cinematic)"),
    ("1:1", "1:1"),
]


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
    if epg is None or display is None or not channel.tvg_id:
        return None, None
    return display.now_and_next(epg, channel.tvg_id, now, channel_name=channel.name)


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
    display: EpgDisplay | None = None,
    epg_shifts_path: Path | None = None,
) -> int:
    player = Player()
    hide_timer: threading.Timer | None = None
    resize_timer: threading.Timer | None = None
    last_mouse_trigger = float("-inf")
    guide_visible = False
    guide_window_start: datetime | None = None
    selected_channel_url: str | None = None
    details_visible = False
    aspect_index = 0
    guide_filter = ""
    filter_input_active = False
    filter_input_text = ""

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

    try:
        player.play(url, title=title)
        player.on_key_press("z", cycle_aspect_ratio)  # available for any playback, not just EPG-backed channels

        if channel is not None and display is not None:
            # A real playlist with no discoverable EPG source (e.g. no
            # x-tvg-url/tvg-url at all) still gets the guide/OSD keybindings
            # -- they just report "no data" instead of silently doing
            # nothing, which otherwise looked indistinguishable from the
            # keys not being bound at all.
            epg = epg or Epg()
            logo = fetch_image(channel.tvg_logo)

            def show_epg_overlay() -> None:
                nonlocal hide_timer
                if guide_visible:
                    # 'i' means "show info" everywhere else in the app; while
                    # the guide is up, that's the selected programme's details.
                    show_selected_details()
                    return
                cancel_hide_timer()

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
                    channel, current, upcoming, display, now, logo=logo, canvas_width=canvas_width, badges=badges
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
                if not guide_filter:
                    return base
                needle = guide_filter.lower()
                return [c for c in base if needle in c.name.lower()]

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
                )
                if image is None:
                    if guide_filter:
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

                render_and_show_guide()
                player.show_text(f"{selected_channel.name} shift: {format_time_shift(new_shift)}", duration_ms=1500)

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

            def unbind_guide_navigation_keys() -> None:
                for key in (*_GUIDE_NAV_ONLY_KEYS, "ENTER", "KP_ENTER", "f", "c"):
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
                # (it covers every letter, including g/i/z's normal meanings).
                player.on_key_press("g", toggle_guide)
                player.on_key_press("i", show_epg_overlay)
                player.on_key_press("z", cycle_aspect_ratio)
                bind_guide_navigation_keys()
                reset_guide_selection()
                render_and_show_guide()

            def confirm_guide_filter() -> None:
                nonlocal guide_filter
                guide_filter = filter_input_text.strip()
                finish_filter_input()

            def cancel_guide_filter() -> None:
                finish_filter_input()

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

            def clear_guide_filter() -> None:
                nonlocal guide_filter
                if not guide_visible or details_visible or filter_input_active or not guide_filter:
                    return  # 'c' is only bound while the guide is open, like the other guide keys
                guide_filter = ""
                reset_guide_selection()
                render_and_show_guide()

            def close_details() -> None:
                nonlocal details_visible
                if not details_visible:
                    return
                player.clear_overlay(overlay_id=_DETAILS_OVERLAY_ID)
                player.unbind_key("ESC")
                details_visible = False

            def show_selected_details() -> None:
                nonlocal details_visible
                if not guide_visible or details_visible or selected_channel_url is None:
                    return

                selected_channel = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                if selected_channel is None:
                    return
                reference_time = guide_reference_time(datetime.now(timezone.utc), resolved_guide_window_start())
                programme = selected_guide_programme(
                    epg, selected_channel.tvg_id, reference_time, shift=display.shift_for(selected_channel.name)
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
                player.on_key_press("ESC", close_details)  # only bound while the popup is open

            def close_guide() -> None:
                nonlocal guide_visible
                if not guide_visible:
                    return
                close_details()
                player.clear_overlay(overlay_id=_GUIDE_OVERLAY_ID)
                unbind_guide_navigation_keys()
                player.on_key_press("ENTER", show_epg_overlay)  # restore the base binding just removed above
                guide_visible = False

            def switch_to_selected_channel() -> None:
                nonlocal channel, logo
                if not guide_visible or selected_channel_url is None:
                    return
                new_channel = next((c for c in guide_channel_list() if c.url == selected_channel_url), None)
                if new_channel is None:
                    return

                close_guide()
                channel = new_channel
                logo = fetch_image(channel.tvg_logo)
                player.play(channel.url, title=channel.name)
                show_epg_overlay()

            def toggle_guide() -> None:
                nonlocal guide_visible, guide_window_start, selected_channel_url, guide_filter
                if guide_visible:
                    close_guide()
                    return

                # Showing the guide replaces the small info banner rather than
                # layering on top of it, and always opens on the current time
                # with any previous filter cleared.
                cancel_hide_timer()
                player.clear_overlay()
                guide_window_start = None
                guide_filter = ""

                visible = visible_guide_channels(guide_channel_list(), epg, channel.url, max_rows=_GUIDE_MAX_ROWS)
                urls = [c.url for c in visible]
                selected_channel_url = channel.url if channel.url in urls else (urls[0] if urls else None)

                if render_and_show_guide():
                    guide_visible = True
                    bind_guide_navigation_keys()

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

        player.wait_for_playback()
    except KeyboardInterrupt:
        pass
    finally:
        cancel_hide_timer()
        cancel_resize_timer()
        player.quit()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tvdinner",
        description="Play IPTV streams from an M3U playlist or a direct stream URL.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    epg_shifts_path = Path(args.epg_shifts) if args.epg_shifts else DEFAULT_CHANNEL_SHIFTS_PATH
    channel_shifts, shift_warnings = load_channel_shifts(epg_shifts_path)
    for warning in shift_warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    try:
        display = EpgDisplay(
            timezone=resolve_timezone(args.tz),
            default_shift=parse_time_shift(args.time_shift) if args.time_shift else timedelta(),
            channel_shifts=channel_shifts,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    playlist = load_playlist(args.url)

    if playlist is None:
        # Doesn't look like an M3U playlist -- treat it as a direct stream URL.
        return play_stream(args.url)

    if not playlist.channels:
        print("No channels found in playlist.", file=sys.stderr)
        return 1

    # Fetched unconditionally: EPG data is also shown as an OSD overlay during
    # playback, not just in the channel listing. When the playlist has no EPG
    # source at all this resolves to no network call and returns None.
    epg = load_epg_for_playlist(playlist, override=args.epg)

    if args.list:
        print_channel_list(playlist.channels, epg=epg, display=display)
        return 0

    if args.channel:
        channel = select_channel(playlist.channels, args.channel)
        if channel is None:
            print(f"Channel not found: {args.channel}", file=sys.stderr)
            return 1
    else:
        channel = playlist.channels[0]

    return play_stream(
        channel.url,
        title=channel.name,
        channel=channel,
        channels=playlist.channels,
        epg=epg,
        display=display,
        epg_shifts_path=epg_shifts_path,
    )


if __name__ == "__main__":
    sys.exit(main())
