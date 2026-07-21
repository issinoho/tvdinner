"""XMLTV EPG parsing with timezone-aware scheduling.

EPG sources are resolved from the M3U playlist itself where possible (the
x-tvg-url/url-tvg attribute on the #EXTM3U header, or per-channel tvg-url
attributes), with an optional explicit override for providers who deliver
the guide separately from the playlist.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from tvdinner.m3u import Playlist

if sys.platform == "win32":
    DEFAULT_CHANNEL_SHIFTS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "epg_shifts.json"
else:
    DEFAULT_CHANNEL_SHIFTS_PATH = Path.home() / ".config" / "tvdinner" / "epg_shifts.json"

_XMLTV_TIME_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*(?:([+-]\d{2})(\d{2}))?$"
)
_SHIFT_RE = re.compile(r"^([+-]?)(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE)


def parse_xmltv_time(value: str) -> datetime:
    """Parse an XMLTV timestamp (e.g. '20260716190000 +0100') into an aware
    datetime. Per the XMLTV spec, a missing UTC offset means the time is
    already in UTC."""
    match = _XMLTV_TIME_RE.match(value.strip())
    if not match:
        raise ValueError(f"Invalid XMLTV timestamp: {value!r}")
    year, month, day, hour, minute, second, off_hours, off_minutes = match.groups()
    if off_hours is None:
        tzinfo = timezone.utc
    else:
        sign = -1 if off_hours.startswith("-") else 1
        offset = sign * timedelta(hours=abs(int(off_hours)), minutes=int(off_minutes))
        tzinfo = timezone(offset)
    return datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=tzinfo
    )


def _parse_release_year(value: str | None) -> str | None:
    """Extract a 4-digit year from a <programme><date> value. Per the
    XMLTV spec this is a release/production date, not a broadcast time --
    real feeds report it as a full date ('1948-06-09'), a year+month, or
    just a year ('1934'); only the leading 4 digits are ever wanted for
    display, regardless of what (if anything) follows."""
    if not value:
        return None
    match = re.match(r"(\d{4})", value.strip())
    return match.group(1) if match else None


def parse_time_shift(value: str) -> timedelta:
    """Parse a user-supplied clock-correction shift: '+1h30m', '-45m', or a
    plain integer taken as minutes."""
    text = value.strip()
    if not text:
        return timedelta()

    match = _SHIFT_RE.match(text)
    if match and (match.group(2) or match.group(3)):
        sign = -1 if match.group(1) == "-" else 1
        hours = int(match.group(2) or 0)
        minutes = int(match.group(3) or 0)
        return sign * timedelta(hours=hours, minutes=minutes)

    try:
        return timedelta(minutes=int(text))
    except ValueError:
        raise ValueError(
            f"Invalid time shift: {value!r} (expected e.g. '+1h30m', '-45m', or minutes as an integer)"
        ) from None


def resolve_timezone(name: str | None) -> ZoneInfo | None:
    """Resolve an IANA timezone name. None means 'use system local time'."""
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Unknown timezone: {name!r}") from None


def load_channel_shifts(path: Path) -> tuple[dict[str, timedelta], list[str]]:
    """Load per-channel EPG clock-correction overrides from a JSON file
    mapping a channel's display name (M3U tvg-name, e.g. as shown by --list)
    to a shift string (same format as parse_time_shift, e.g. '+1h', '-30m'),
    e.g.:

        {"BBC One": "+1h", "TCM US West": "-3h"}

    Keyed by name rather than tvg_id because real-world playlists commonly
    have several distinct channels (e.g. regional feeds like an East/West
    coast pair) sharing one tvg_id for EPG mapping, which a tvg_id-keyed
    override couldn't tell apart.

    A missing file is not an error (most users won't have one) -- it just
    means no overrides. Malformed JSON or individual bad entries are
    reported as warning strings rather than raising, so one typo doesn't
    prevent the whole app from starting; the caller decides how to surface
    them (e.g. printed to stderr).
    """
    if not path.is_file():
        return {}, []

    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"Could not read EPG shifts file {path}: {exc}"]

    if not isinstance(raw, dict):
        return {}, [f"EPG shifts file {path} must contain a JSON object mapping channel name to a shift"]

    shifts: dict[str, timedelta] = {}
    warnings: list[str] = []
    for name, value in raw.items():
        try:
            shifts[name] = parse_time_shift(str(value))
        except ValueError as exc:
            warnings.append(f"Ignoring EPG shift for {name!r} in {path}: {exc}")
    return shifts, warnings


def format_time_shift(delta: timedelta) -> str:
    """Format a timedelta as a shift string parse_time_shift can read back,
    e.g. '+1h30m', '-45m', '+0m'."""
    total_minutes = round(delta.total_seconds() / 60)
    sign = "-" if total_minutes < 0 else "+"
    hours, minutes = divmod(abs(total_minutes), 60)
    if hours and minutes:
        return f"{sign}{hours}h{minutes}m"
    if hours:
        return f"{sign}{hours}h"
    return f"{sign}{minutes}m"


def save_channel_shifts(path: Path, shifts: dict[str, timedelta]) -> None:
    """Write per-channel EPG shift overrides back to a JSON file -- the
    inverse of load_channel_shifts, used by the live '['/']' keybinding to
    persist a nudged shift immediately. Creates the parent directory if
    needed (most users won't have ~/.config/tvdinner yet)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {name: format_time_shift(shift) for name, shift in shifts.items()}
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")


@dataclass
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str | None = None
    category: str | None = None
    poster_url: str | None = None  # from <programme><icon src="..."/>, e.g. movie poster/artwork
    year: str | None = None  # from <programme><date>, e.g. a film's release year

    def is_at(self, moment: datetime) -> bool:
        return self.start <= moment < self.stop


@dataclass
class EpgChannel:
    id: str
    display_names: list[str] = field(default_factory=list)
    icon: str | None = None

    @property
    def name(self) -> str:
        return self.display_names[0] if self.display_names else self.id


@dataclass
class Epg:
    channels: dict[str, EpgChannel] = field(default_factory=dict)
    programmes: dict[str, list[Programme]] = field(default_factory=dict)  # channel_id -> sorted by start

    def schedule_for(self, channel_id: str) -> list[Programme]:
        return self.programmes.get(channel_id, [])

    def now_and_next(
        self, channel_id: str, at: datetime
    ) -> tuple[Programme | None, Programme | None]:
        """Return the programme airing at `at` and the one after it, for the
        given channel. `at` must already be corrected for any display shift."""
        schedule = self.schedule_for(channel_id)
        for index, programme in enumerate(schedule):
            if programme.is_at(at):
                upcoming = schedule[index + 1] if index + 1 < len(schedule) else None
                return programme, upcoming
            if programme.start > at:
                return None, programme
        return None, None

    def merge(self, other: "Epg") -> None:
        self.channels.update(other.channels)
        for channel_id, progs in other.programmes.items():
            self.programmes.setdefault(channel_id, []).extend(progs)
        for schedule in self.programmes.values():
            schedule.sort(key=lambda p: p.start)


@dataclass
class EpgDisplay:
    """Presentation settings: what timezone to show EPG times in, and a
    clock-correction shift for feeds whose reported times are simply wrong
    -- a default applied to every channel, with optional per-channel
    overrides (keyed by the channel's display name -- see channel_shifts,
    and load_channel_shifts for why name rather than tvg_id) for feeds
    where different channels are off by different amounts.
    """

    timezone: ZoneInfo | None = None  # None => system local timezone
    default_shift: timedelta = timedelta()
    channel_shifts: dict[str, timedelta] = field(default_factory=dict)

    def shift_for(self, channel_name: str | None) -> timedelta:
        if channel_name and channel_name in self.channel_shifts:
            return self.channel_shifts[channel_name]
        return self.default_shift

    def to_local(self, moment: datetime, channel_name: str | None = None) -> datetime:
        corrected = moment + self.shift_for(channel_name)
        return corrected.astimezone(self.timezone) if self.timezone else corrected.astimezone()

    def now_and_next(
        self, epg: Epg, channel_id: str, at: datetime, channel_name: str | None = None
    ) -> tuple[Programme | None, Programme | None]:
        return epg.now_and_next(channel_id, at - self.shift_for(channel_name))


def parse_xmltv(data: bytes | str) -> Epg:
    root = ElementTree.fromstring(data)
    epg = Epg()

    for channel_el in root.findall("channel"):
        channel_id = channel_el.get("id", "")
        if not channel_id:
            continue
        names = [
            el.text.strip()
            for el in channel_el.findall("display-name")
            if el.text and el.text.strip()
        ]
        icon_el = channel_el.find("icon")
        icon = icon_el.get("src") if icon_el is not None else None
        epg.channels[channel_id] = EpgChannel(id=channel_id, display_names=names, icon=icon)

    for prog_el in root.findall("programme"):
        channel_id = prog_el.get("channel", "")
        start_raw = prog_el.get("start")
        stop_raw = prog_el.get("stop")
        if not channel_id or not start_raw or not stop_raw:
            continue
        try:
            start = parse_xmltv_time(start_raw)
            stop = parse_xmltv_time(stop_raw)
        except ValueError:
            continue

        title_el = prog_el.find("title")
        desc_el = prog_el.find("desc")
        category_el = prog_el.find("category")
        icon_el = prog_el.find("icon")
        date_el = prog_el.find("date")
        programme = Programme(
            channel_id=channel_id,
            start=start,
            stop=stop,
            title=(title_el.text or "").strip() if title_el is not None else "",
            description=(desc_el.text.strip() if desc_el is not None and desc_el.text else None),
            category=(category_el.text.strip() if category_el is not None and category_el.text else None),
            poster_url=(icon_el.get("src") or None) if icon_el is not None else None,
            year=_parse_release_year(date_el.text) if date_el is not None else None,
        )
        epg.programmes.setdefault(channel_id, []).append(programme)

    for schedule in epg.programmes.values():
        schedule.sort(key=lambda p: p.start)

    return epg


def _maybe_decompress(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":  # gzip magic number; some XMLTV feeds serve .xml.gz bodies
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


def _fetch_bytes(source: str) -> bytes | None:
    parsed = urllib.parse.urlparse(source)

    if parsed.scheme in ("http", "https"):
        try:
            response = requests.get(source, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            return None
        return response.content

    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme == "file" else source)
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError:
                return None
        return None

    return None


def load_epg(source: str) -> Epg | None:
    """Fetch and parse an XMLTV EPG document from an http(s) URL or local
    file path (transparently gzip-decompressed if needed)."""
    data = _fetch_bytes(source)
    if data is None:
        return None
    data = _maybe_decompress(data)
    try:
        return parse_xmltv(data)
    except ElementTree.ParseError:
        return None


def split_epg_sources(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def resolve_epg_sources(playlist: Playlist, override: str | None = None) -> list[str]:
    """Determine which XMLTV URL(s) to load EPG data from: an explicit
    override wins, otherwise the playlist's own embedded EPG reference is
    used, so the guide is drawn directly from the M3U data with no extra
    configuration required."""
    if override:
        return split_epg_sources(override)
    if playlist.epg_url:
        return split_epg_sources(playlist.epg_url)

    sources: list[str] = []
    for channel in playlist.channels:
        if channel.tvg_url and channel.tvg_url not in sources:
            sources.append(channel.tvg_url)
    return sources


def load_epg_for_playlist(playlist: Playlist, override: str | None = None) -> Epg | None:
    sources = resolve_epg_sources(playlist, override)
    if not sources:
        return None

    merged = Epg()
    loaded_any = False
    for source in sources:
        epg = load_epg(source)
        if epg is not None:
            merged.merge(epg)
            loaded_any = True
    return merged if loaded_any else None
