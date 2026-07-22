"""XMLTV EPG parsing with timezone-aware scheduling.

EPG sources are resolved from the M3U playlist itself where possible (the
x-tvg-url/url-tvg attribute on the #EXTM3U header, or per-channel tvg-url
attributes), with an optional explicit override for providers who deliver
the guide separately from the playlist.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import pickle
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from tvdinner.m3u import Playlist

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    DEFAULT_CHANNEL_SHIFTS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "epg_shifts.json"
    DEFAULT_EPG_CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "tvdinner" / "epg_cache"
else:
    DEFAULT_CHANNEL_SHIFTS_PATH = Path.home() / ".config" / "tvdinner" / "epg_shifts.json"
    DEFAULT_EPG_CACHE_DIR = Path.home() / ".cache" / "tvdinner" / "epg"

# "Once a day" by default: large real-world EPG feeds can be hundreds of MB
# and take tens of seconds to download and parse, so re-fetching on every
# startup is wasteful when the guide data hasn't meaningfully changed since
# yesterday.
DEFAULT_EPG_CACHE_MAX_AGE = timedelta(hours=24)

_XMLTV_TIME_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*(?:([+-]\d{2})(\d{2}))?$"
)
_SHIFT_RE = re.compile(r"^([+-]?)(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE)

# iptv-org's own playlists append a '@feed' tag (e.g. '@SD', '@HD', '@East')
# to their canonical channel id to disambiguate multiple streams for one
# channel; the EPG source has no reason to know about that tag, so a tvg_id
# lookup that fails verbatim is retried with it stripped.
_FEED_SUFFIX_RE = re.compile(r"@[^@]+$")

# Some XMLTV providers prefix every display-name with their own source tag
# (e.g. "PLUTO - 00s Replay", "SXM - ..."), which a plain tvg_id/display-name
# match would never see past. Only strips a tag followed by a *spaced* hyphen
# so hyphenated names like "24-Hour News" aren't mistaken for one.
_NAME_SOURCE_TAG_RE = re.compile(r"^[A-Za-z0-9]+\s+-\s+")


def _normalize_name(name: str) -> str:
    text = _NAME_SOURCE_TAG_RE.sub("", name.strip())
    return re.sub(r"\s+", " ", text).strip().lower()


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
    _name_index: dict[str, str] | None = field(default=None, init=False, repr=False, compare=False)

    def _channel_id_by_name(self) -> dict[str, str]:
        """Lazily-built, cached index of normalized display-name -> channel
        id, so a name-based fallback lookup is an O(1) dict access rather
        than scanning every EPG channel on every call (this is consulted on
        every overlay/guide render, not just once at load time)."""
        if self._name_index is None:
            index: dict[str, str] = {}
            for channel_id, epg_channel in self.channels.items():
                for name in epg_channel.display_names:
                    key = _normalize_name(name)
                    if key and key not in index:
                        index[key] = channel_id
            self._name_index = index
        return self._name_index

    def resolve_channel_id(self, tvg_id: str | None, name: str | None = None) -> str | None:
        """Resolve an M3U channel's tvg_id/display name to the id the loaded
        EPG actually keys its channels/programmes by. Tries, in order: an
        exact tvg_id match, the tvg_id with a trailing '@feed' tag stripped
        (see _FEED_SUFFIX_RE), then a normalized display-name match."""
        if tvg_id:
            if tvg_id in self.programmes or tvg_id in self.channels:
                return tvg_id
            stripped = _FEED_SUFFIX_RE.sub("", tvg_id)
            if stripped != tvg_id and (stripped in self.programmes or stripped in self.channels):
                return stripped
        if name:
            return self._channel_id_by_name().get(_normalize_name(name))
        return None

    def schedule_for(self, channel_id: str | None, name: str | None = None) -> list[Programme]:
        resolved = self.resolve_channel_id(channel_id, name)
        return self.programmes.get(resolved, []) if resolved else []

    def now_and_next(
        self, channel_id: str | None, at: datetime, name: str | None = None
    ) -> tuple[Programme | None, Programme | None]:
        """Return the programme airing at `at` and the one after it, for the
        given channel. `at` must already be corrected for any display shift."""
        schedule = self.schedule_for(channel_id, name)
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
            schedule = self.programmes.setdefault(channel_id, [])
            schedule.extend(progs)
            schedule.sort(key=lambda p: p.start)
        self._name_index = None


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
        self,
        epg: Epg,
        channel_id: str | None,
        at: datetime,
        channel_name: str | None = None,
        match_name: str | None = None,
    ) -> tuple[Programme | None, Programme | None]:
        """`channel_name` is used only to look up this channel's clock-shift
        override (see shift_for); `match_name` is a separate, optional name
        to try for EPG channel-id resolution when `channel_id` alone doesn't
        match (see Epg.resolve_channel_id)."""
        return epg.now_and_next(channel_id, at - self.shift_for(channel_name), name=match_name)


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
        except requests.RequestException as exc:
            logger.warning("Could not fetch EPG %s: %s", source, exc)
            return None
        return response.content

    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme == "file" else source)
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError as exc:
                logger.warning("Could not read EPG %s: %s", path, exc)
                return None
        return None

    return None


def _cache_path_for(cache_dir: Path, source: str) -> Path:
    return cache_dir / f"{hashlib.sha256(source.encode()).hexdigest()}.xml"


def _parsed_cache_path_for(cache_dir: Path, source: str) -> Path:
    return cache_dir / f"{hashlib.sha256(source.encode()).hexdigest()}.pkl"


def _load_cached_parsed_epg(source: str, cache_dir: Path, max_age: timedelta) -> Epg | None:
    """A fresh raw-bytes cache hit still costs a full XML parse on every
    startup; this caches the already-parsed Epg (pickled) next to the raw
    cache so a hit skips parsing too. Only trusted when the raw cache is
    itself still fresh and the pickle is at least as new as it, so a live
    re-fetch or a stale-cache-fallback (see _fetch_bytes_cached) can never
    have its result masked by parsed data left over from a previous body."""
    raw_path = _cache_path_for(cache_dir, source)
    parsed_path = _parsed_cache_path_for(cache_dir, source)
    if not raw_path.is_file() or not parsed_path.is_file():
        return None
    try:
        raw_mtime = raw_path.stat().st_mtime
        if timedelta(seconds=time.time() - raw_mtime) >= max_age:
            return None
        if parsed_path.stat().st_mtime < raw_mtime:
            return None
        with parsed_path.open("rb") as fh:
            epg = pickle.load(fh)
    except Exception as exc:
        # Corrupt pickle, or one written by a since-changed version of this
        # module (renamed/retyped field) -- either way, silently re-parse
        # rather than let a cache artifact break EPG loading.
        logger.warning("Discarding unreadable parsed-EPG cache for %s: %s", source, exc)
        return None
    return epg if isinstance(epg, Epg) else None


def _save_cached_parsed_epg(source: str, cache_dir: Path, epg: Epg) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with _parsed_cache_path_for(cache_dir, source).open("wb") as fh:
            pickle.dump(epg, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except (OSError, pickle.PicklingError) as exc:
        logger.warning("Could not write parsed-EPG cache for %s: %s", source, exc)


def _fetch_bytes_cached(source: str, cache_dir: Path, max_age: timedelta) -> bytes | None:
    """Like _fetch_bytes, but for http(s) sources transparently caches the
    downloaded body on disk (keyed by URL) and reuses it without touching
    the network at all while younger than `max_age` -- large real-world EPG
    feeds can take tens of seconds to download, so this keeps ordinary
    startups (same feed as last time) fast. A stale cache is used as a
    fallback if the network fetch fails, rather than losing EPG data
    entirely over a transient connectivity problem. Local file/path sources
    are already fast to read and are never cached."""
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme not in ("http", "https"):
        return _fetch_bytes(source)

    cache_path = _cache_path_for(cache_dir, source)
    if cache_path.is_file():
        age = timedelta(seconds=time.time() - cache_path.stat().st_mtime)
        if age < max_age:
            try:
                return cache_path.read_bytes()
            except OSError:
                pass

    data = _fetch_bytes(source)
    if data is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        except OSError:
            pass
        return data

    try:
        return cache_path.read_bytes() if cache_path.is_file() else None
    except OSError:
        return None


def load_epg(
    source: str, cache_dir: Path | None = None, max_age: timedelta = DEFAULT_EPG_CACHE_MAX_AGE
) -> Epg | None:
    """Fetch and parse an XMLTV EPG document from an http(s) URL or local
    file path (transparently gzip-decompressed if needed). `cache_dir`
    enables on-disk caching of http(s) sources -- see _fetch_bytes_cached
    and _load_cached_parsed_epg."""
    if cache_dir:
        cached = _load_cached_parsed_epg(source, cache_dir, max_age)
        if cached is not None:
            return cached

    data = _fetch_bytes_cached(source, cache_dir, max_age) if cache_dir else _fetch_bytes(source)
    if data is None:
        return None
    data = _maybe_decompress(data)
    try:
        epg = parse_xmltv(data)
    except ElementTree.ParseError as exc:
        logger.warning("Could not parse EPG %s: %s", source, exc)
        return None
    if cache_dir:
        _save_cached_parsed_epg(source, cache_dir, epg)
    return epg


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


def load_epg_for_playlist(
    playlist: Playlist,
    override: str | None = None,
    cache_dir: Path | None = DEFAULT_EPG_CACHE_DIR,
    max_age: timedelta = DEFAULT_EPG_CACHE_MAX_AGE,
) -> Epg | None:
    sources = resolve_epg_sources(playlist, override)
    if not sources:
        return None

    merged = Epg()
    loaded_any = False
    for source in sources:
        epg = load_epg(source, cache_dir=cache_dir, max_age=max_age)
        if epg is not None:
            merged.merge(epg)
            loaded_any = True
    return merged if loaded_any else None
