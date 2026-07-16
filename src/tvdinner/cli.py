"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime, timedelta, timezone

from tvdinner.epg import Epg, EpgDisplay, Programme, load_epg_for_playlist, parse_time_shift, resolve_timezone
from tvdinner.m3u import Channel, load_playlist
from tvdinner.overlay import fetch_logo, render_epg_overlay
from tvdinner.player import Player

_OVERLAY_MARGIN = 40
_OVERLAY_HIDE_AFTER_SECONDS = 6.0


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
    epg: Epg | None = None,
    display: EpgDisplay | None = None,
) -> int:
    player = Player()
    hide_timer: threading.Timer | None = None

    def cancel_hide_timer() -> None:
        nonlocal hide_timer
        if hide_timer is not None:
            hide_timer.cancel()
            hide_timer = None

    try:
        player.play(url, title=title)

        if channel is not None and epg is not None and display is not None:
            logo = fetch_logo(channel.tvg_logo)

            def show_epg_overlay() -> None:
                nonlocal hide_timer
                cancel_hide_timer()

                now = datetime.now(timezone.utc)
                current, upcoming = current_and_next_programmes(channel, epg, display, now)
                if current is None and upcoming is None:
                    return

                video_size = player.video_size()
                canvas_width = video_size[0] if video_size else 1920
                image = render_epg_overlay(
                    channel, current, upcoming, display, now, logo=logo, canvas_width=canvas_width
                )
                player.show_overlay(image, x=_OVERLAY_MARGIN, y=_OVERLAY_MARGIN)

                hide_timer = threading.Timer(_OVERLAY_HIDE_AFTER_SECONDS, player.clear_overlay)
                hide_timer.daemon = True
                hide_timer.start()

            show_epg_overlay()
            player.on_key_press("i", show_epg_overlay)  # press 'i' anytime to (re-)show EPG info

        player.wait_for_playback()
    except KeyboardInterrupt:
        pass
    finally:
        cancel_hide_timer()
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

    return play_stream(channel.url, title=channel.name, channel=channel, epg=epg, display=display)


if __name__ == "__main__":
    sys.exit(main())
