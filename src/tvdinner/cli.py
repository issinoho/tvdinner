"""Command-line entry point for tvdinner."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from tvdinner.epg import Epg, EpgDisplay, load_epg_for_playlist, parse_time_shift, resolve_timezone
from tvdinner.m3u import Channel, load_playlist
from tvdinner.player import Player


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

    if epg is not None and display is not None and channel.tvg_id:
        current, upcoming = display.now_and_next(epg, channel.tvg_id, now)
        parts = []
        if current:
            start = display.to_local(current.start).strftime("%H:%M")
            stop = display.to_local(current.stop).strftime("%H:%M")
            parts.append(f"Now: {current.title} ({start}–{stop})")
        if upcoming:
            start = display.to_local(upcoming.start).strftime("%H:%M")
            parts.append(f"Next: {upcoming.title} ({start})")
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


def play_stream(url: str, title: str | None = None) -> int:
    player = Player()
    try:
        player.play(url, title=title)
        player.wait_for_playback()
    except KeyboardInterrupt:
        pass
    finally:
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

    # Only fetch EPG data when it'll actually be shown, so playing a channel
    # directly via --channel doesn't pay for an EPG fetch it won't use.
    needs_listing = args.list or not args.channel
    epg = load_epg_for_playlist(playlist, override=args.epg) if needs_listing else None

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

    return play_stream(channel.url, title=channel.name)


if __name__ == "__main__":
    sys.exit(main())
