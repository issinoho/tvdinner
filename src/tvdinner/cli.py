"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from tvdinner.epg import Epg, EpgDisplay, Programme, load_epg_for_playlist, parse_time_shift, resolve_timezone
from tvdinner.m3u import Channel, load_playlist
from tvdinner.overlay import fetch_logo, render_epg_overlay, render_program_guide
from tvdinner.player import Player

_OVERLAY_TOP_MARGIN = 40
_OVERLAY_HIDE_AFTER_SECONDS = 6.0
_OVERLAY_RESIZE_DEBOUNCE_SECONDS = 0.2
_OVERLAY_MOUSE_MOVE_THROTTLE_SECONDS = 1.0
_GUIDE_OVERLAY_ID = 1
_DEFAULT_CANVAS_WIDTH = 1920
_DEFAULT_CANVAS_HEIGHT = 1080
_OSD_SIZE_WAIT_SECONDS = 2.0
_OSD_SIZE_POLL_INTERVAL = 0.05


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
    return display.now_and_next(epg, channel.tvg_id, now)


def now_and_next_text(
    channel: Channel, epg: Epg | None, display: EpgDisplay | None, now: datetime
) -> tuple[str | None, str | None]:
    """Format the current and upcoming programme for a channel as
    ('Now: ...', 'Next: ...') strings, whichever are available."""
    current, upcoming = current_and_next_programmes(channel, epg, display, now)
    now_text = None
    next_text = None
    if current:
        start = display.to_local(current.start).strftime("%H:%M")
        stop = display.to_local(current.stop).strftime("%H:%M")
        now_text = f"Now: {current.title} ({start}–{stop})"
    if upcoming:
        start = display.to_local(upcoming.start).strftime("%H:%M")
        next_text = f"Next: {upcoming.title} ({start})"
    return now_text, next_text


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


def prompt_for_channel(channels: list[Channel]) -> Channel | None:
    try:
        raw = input(f"\nSelect a channel [1-{len(channels)}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    return select_channel(channels, raw)


def play_stream(
    url: str,
    title: str | None = None,
    channel: Channel | None = None,
    channels: list[Channel] | None = None,
    epg: Epg | None = None,
    display: EpgDisplay | None = None,
) -> int:
    player = Player()
    hide_timer: threading.Timer | None = None
    resize_timer: threading.Timer | None = None
    last_mouse_trigger = float("-inf")
    guide_visible = False

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

    try:
        player.play(url, title=title)

        if channel is not None and epg is not None and display is not None:
            logo = fetch_logo(channel.tvg_logo)

            def show_epg_overlay() -> None:
                nonlocal hide_timer
                if guide_visible:
                    return  # the full guide is up; don't clutter it with the small banner
                cancel_hide_timer()

                now = datetime.now(timezone.utc)
                current, upcoming = current_and_next_programmes(channel, epg, display, now)
                if current is None and upcoming is None:
                    return

                canvas_width = _resolve_canvas_width(player)
                image = render_epg_overlay(
                    channel, current, upcoming, display, now, logo=logo, canvas_width=canvas_width
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

            def toggle_guide() -> None:
                nonlocal guide_visible
                if guide_visible:
                    player.clear_overlay(overlay_id=_GUIDE_OVERLAY_ID)
                    guide_visible = False
                    return

                # Showing the guide replaces the small info banner rather than
                # layering on top of it.
                cancel_hide_timer()
                player.clear_overlay()

                osd_size = player.osd_size() or (_DEFAULT_CANVAS_WIDTH, _DEFAULT_CANVAS_HEIGHT)
                image = render_program_guide(
                    channels or [channel],
                    epg,
                    display,
                    datetime.now(timezone.utc),
                    current_channel_id=channel.tvg_id,
                    canvas_width=osd_size[0],
                    canvas_height=osd_size[1],
                )
                if image is None:
                    player.show_text("No programme guide data available", duration_ms=3000)
                    return

                x = (osd_size[0] - image.width) // 2
                y = (osd_size[1] - image.height) // 2
                player.show_overlay(image, x=x, y=y, overlay_id=_GUIDE_OVERLAY_ID)
                guide_visible = True

            show_epg_overlay()
            player.on_key_press("i", show_epg_overlay)  # press 'i' anytime to (re-)show EPG info
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
        "url",
        help="M3U/M3U8 playlist URL or local file path, or a direct video/audio stream URL",
    )
    parser.add_argument(
        "-c",
        "--channel",
        help="Channel name (or 1-based index) to play directly, skipping interactive selection",
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
        help="Correct EPG feed clock errors, e.g. '+1h', '-30m', or minutes as a plain integer",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        display = EpgDisplay(
            timezone=resolve_timezone(args.tz),
            shift=parse_time_shift(args.time_shift) if args.time_shift else timedelta(),
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
        print_channel_list(playlist.channels, epg=epg, display=display)
        channel = prompt_for_channel(playlist.channels)
        if channel is None:
            print("No channel selected.", file=sys.stderr)
            return 1

    return play_stream(
        channel.url, title=channel.name, channel=channel, channels=playlist.channels, epg=epg, display=display
    )


if __name__ == "__main__":
    sys.exit(main())
