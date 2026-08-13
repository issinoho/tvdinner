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
import tempfile
import time
import unicodedata
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from tvdinner import __version__
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

_DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1MB -- fine-grained enough for a smooth byte counter, coarse enough to keep callback overhead negligible

# (bytes_downloaded_so_far, total_bytes_or_None -- None when the server
# doesn't send a Content-Length, e.g. a chunked-transfer response, which is
# common for large dynamically-generated XMLTV feeds -- confirmed live
# against a real 400+MB feed served exactly this way).
ProgressCallback = Callable[[int, int | None], None]

_XMLTV_TIME_RE = re.compile(
    r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\s*(?:([+-]\d{2})(\d{2}))?$"
)
_SHIFT_RE = re.compile(r"^([+-]?)(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE)

# iptv-org's own playlists append a '@feed' tag (e.g. '@SD', '@HD', '@East')
# to their canonical channel id to disambiguate multiple streams for one
# channel; the EPG source has no reason to know about that tag, so a tvg_id
# lookup that fails verbatim is retried with it stripped.
FEED_SUFFIX_RE = re.compile(r"@[^@]+$")

# Some XMLTV providers prefix every display-name with their own source tag
# (e.g. "PLUTO - 00s Replay", "SXM - ..."), which a plain tvg_id/display-name
# match would never see past. Only strips a tag followed by a *spaced* hyphen
# so hyphenated names like "24-Hour News" aren't mistaken for one.
_NAME_SOURCE_TAG_RE = re.compile(r"^[A-Za-z0-9]+\s+-\s+")


def _strip_trailing_decoration(text: str) -> str:
    """Drop a trailing decorative marker some playlist generators append to
    a channel's display name (e.g. a circled letter or emoji flag, seemingly
    a "guide available" indicator) -- it isn't part of the real name and
    would otherwise never match the EPG's own (undecorated) one. Only
    Unicode Symbol-category characters (So/Sm/Sk/Sc, which covers most
    emoji and circled/boxed glyphs) are stripped, not general punctuation
    like the parens in "Channel (East)" or the hyphen in "24-Hour News"."""
    while text and (text[-1].isspace() or unicodedata.category(text[-1]).startswith("S")):
        text = text[:-1]
    return text


def normalize_name(name: str) -> str:
    text = _NAME_SOURCE_TAG_RE.sub("", name.strip())
    text = _strip_trailing_decoration(text)
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


_EPISODE_MARKER_RE = re.compile(r"^S\d+\s*E\d+\s*", re.IGNORECASE)


def _strip_episode_marker(description: str) -> str:
    """Some feeds prefix the <desc> text with a redundant 'S1 E1' season/
    episode marker (that info is already available structurally); drop it
    so the overlay doesn't show it twice."""
    return _EPISODE_MARKER_RE.sub("", description)


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
                    key = normalize_name(name)
                    if key and key not in index:
                        index[key] = channel_id
            self._name_index = index
        return self._name_index

    def resolve_channel_id(self, tvg_id: str | None, name: str | None = None) -> str | None:
        """Resolve an M3U channel's tvg_id/display name to the id the loaded
        EPG actually keys its channels/programmes by. Tries, in order: an
        exact tvg_id match, the tvg_id with a trailing '@feed' tag stripped
        (see FEED_SUFFIX_RE), then a normalized display-name match."""
        if tvg_id:
            if tvg_id in self.programmes or tvg_id in self.channels:
                return tvg_id
            stripped = FEED_SUFFIX_RE.sub("", tvg_id)
            if stripped != tvg_id and (stripped in self.programmes or stripped in self.channels):
                return stripped
        if name:
            return self._channel_id_by_name().get(normalize_name(name))
        return None

    def schedule_for(self, channel_id: str | None, name: str | None = None) -> list[Programme]:
        resolved = self.resolve_channel_id(channel_id, name)
        return self.programmes.get(resolved, []) if resolved else []

    def icon_for(self, channel_id: str | None, name: str | None = None) -> str | None:
        """The channel logo URL the EPG feed itself supplies (its
        <channel><icon src="..."/>), for sources with no per-channel logo
        of their own -- e.g. HDHomeRun's lineup.json has no logo field at
        all, but SiliconDust's XMLTV export does. Callers should prefer
        their own Channel.tvg_logo when present and only fall back to this
        (see cli.py/overlay.py's `channel.tvg_logo or epg.icon_for(...)`)."""
        resolved = self.resolve_channel_id(channel_id, name)
        channel = self.channels.get(resolved) if resolved else None
        return channel.icon if channel else None

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
    # A full ElementTree.fromstring() DOM keeps every channel/programme
    # element -- descriptions, credits, actor lists, icon URLs, everything
    # -- resident at once; for a large real-world feed (tens of thousands of
    # programmes) that DOM alone can run into gigabytes, on top of the raw
    # bytes and the Epg being built from it. iterparse processes one
    # top-level element at a time; elem.clear() drops its own subtree once
    # we've pulled what we need from it, and root.clear() drops the (by
    # then empty) reference the root would otherwise keep accumulating for
    # every channel/programme seen so far -- ElementTree has no parent-link
    # API to remove just the one element, unlike lxml.
    source = BytesIO(data) if isinstance(data, bytes) else StringIO(data)
    epg = Epg()

    context = iter(ElementTree.iterparse(source, events=("start", "end")))
    _, root = next(context)

    for event, elem in context:
        if event != "end":
            continue

        if elem.tag == "channel":
            channel_id = elem.get("id", "")
            if channel_id:
                names = [
                    el.text.strip()
                    for el in elem.findall("display-name")
                    if el.text and el.text.strip()
                ]
                icon_el = elem.find("icon")
                icon = icon_el.get("src") if icon_el is not None else None
                # Some providers (e.g. SiliconDust's HDHomeRun XMLTV export)
                # emit several <channel> elements sharing one id -- one per
                # SD/HD simulcast or regional variant of the same underlying
                # station, each with its own display-name spelling. A plain
                # overwrite here would silently drop every name variant but
                # the last one parsed, breaking the name-based fallback
                # match (see Epg.resolve_channel_id) for any channel whose
                # tvg_id doesn't hit an exact id match -- merge instead.
                existing = epg.channels.get(channel_id)
                if existing is None:
                    epg.channels[channel_id] = EpgChannel(id=channel_id, display_names=names, icon=icon)
                else:
                    existing.display_names.extend(n for n in names if n not in existing.display_names)
                    if existing.icon is None:
                        existing.icon = icon
        elif elem.tag == "programme":
            channel_id = elem.get("channel", "")
            start_raw = elem.get("start")
            stop_raw = elem.get("stop")
            start = stop = None
            if channel_id and start_raw and stop_raw:
                try:
                    start = parse_xmltv_time(start_raw)
                    stop = parse_xmltv_time(stop_raw)
                except ValueError:
                    start = stop = None
            if start is not None:
                title_el = elem.find("title")
                desc_el = elem.find("desc")
                icon_el = elem.find("icon")
                date_el = elem.find("date")
                # XMLTV allows several <category> tags per programme (e.g. a
                # genre plus "Movie") -- joining all of them (rather than
                # elem.find's single first match) is what lets
                # tmdb.is_movie_category actually see the "Movie" one when a
                # feed lists the more specific genre first.
                categories = [c.text.strip() for c in elem.findall("category") if c.text and c.text.strip()]
                programme = Programme(
                    channel_id=channel_id,
                    start=start,
                    stop=stop,
                    title=(title_el.text or "").strip() if title_el is not None else "",
                    description=(
                        _strip_episode_marker(desc_el.text.strip())
                        if desc_el is not None and desc_el.text
                        else None
                    ),
                    category=(", ".join(categories) or None),
                    poster_url=(icon_el.get("src") or None) if icon_el is not None else None,
                    year=_parse_release_year(date_el.text) if date_el is not None else None,
                )
                epg.programmes.setdefault(channel_id, []).append(programme)
        else:
            continue  # a nested element (title/desc/credits/...); its parent's clear() below takes care of it

        elem.clear()
        root.clear()

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


def _fetch_bytes(source: str, on_progress: ProgressCallback | None = None) -> bytes | None:
    parsed = urllib.parse.urlparse(source)

    if parsed.scheme in ("http", "https"):
        # Streamed rather than a single response.content read -- real-world
        # EPG feeds (and, via fetch_bytes_cached, iptv-org's channel/logo
        # database) can run into the hundreds of MB, and `on_progress`
        # (wired up by cli.py to a periodic "Loading EPG data... (N MB
        # downloaded)" message) is what keeps that from looking like a
        # hung terminal partway through a multi-minute download. The
        # `timeout` here still applies per socket read, not to the request
        # as a whole -- confirmed live that a real feed with no
        # Content-Length (chunked transfer-encoding, so `total` is None
        # below) still completes correctly as long as each individual read
        # keeps arriving within the timeout, however long that takes
        # overall.
        try:
            with requests.get(source, timeout=20, stream=True) as response:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                total = int(content_length) if content_length and content_length.isdigit() else None
                chunks = []
                downloaded = 0
                for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    downloaded += len(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)
        except requests.RequestException as exc:
            logger.warning("Could not fetch EPG %s: %s", source, exc)
            return None
        return b"".join(chunks)

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


def cache_path_for(cache_dir: Path, source: str, suffix: str = ".xml") -> Path:
    """Not XMLTV-specific despite living here -- fetch_bytes_cached (and
    thus this) is reused as-is by tvdinner.channel_logos for a completely
    different cached document (iptv-org's channel/logo database), which is
    why `suffix` isn't hardcoded to ".xml"."""
    return cache_dir / f"{hashlib.sha256(source.encode()).hexdigest()}{suffix}"


def parsed_cache_path_for(cache_dir: Path, source: str) -> Path:
    """The parsed-Epg pickle sibling of cache_path_for's own raw-bytes
    cache file for the same `source` -- public (not just used internally
    by _load_cached_parsed_epg/_save_cached_parsed_epg below) so a caller
    that only needs to know whether/how much is cached for a source, not
    load it, doesn't have to duplicate this naming scheme (see cli.py's
    stats command)."""
    return cache_dir / f"{hashlib.sha256(source.encode()).hexdigest()}.pkl"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically -- via a temp file in the same
    directory, renamed into place (os.replace, atomic on POSIX and
    Windows within one filesystem) only once the write has fully
    completed -- so a process killed mid-write can never leave a
    truncated/corrupt file at the real cache path for the next run to
    trip over. Confirmed live: this is exactly what produced "Discarding
    unreadable parsed-EPG cache ... Ran out of input" against a very
    large (300+ MB), slow-to-parse feed -- the background EPG-loading
    thread is a daemon thread with no graceful-shutdown handling, so
    quitting tvdinner while it's still writing the parsed-cache pickle
    could truncate it mid-write.

    Not EPG-specific despite living here -- reused as-is by
    tvdinner.tmdb for its own on-disk ratings cache, same reasoning as
    cache_path_for above."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _load_cached_parsed_epg(source: str, cache_dir: Path, max_age: timedelta) -> Epg | None:
    """A fresh raw-bytes cache hit still costs a full XML parse on every
    startup; this caches the already-parsed Epg (pickled) next to the raw
    cache so a hit skips parsing too. Only trusted when the raw cache is
    itself still fresh and the pickle is at least as new as it, so a live
    re-fetch or a stale-cache-fallback (see fetch_bytes_cached) can never
    have its result masked by parsed data left over from a previous body.

    Also tagged with the tvdinner version that wrote it (see
    _save_cached_parsed_epg): Epg/Programme's *fields* rarely change, but
    parse_xmltv's parsing logic can (e.g. which XML bits populate a given
    field) without any schema change to trip the pickle-compat check below
    -- confirmed live that upgrading tvdinner otherwise kept silently
    serving programmes parsed by the old code for up to a full
    --epg-cache-hours window post-upgrade. A version mismatch is treated
    the same as a corrupt pickle: re-parse rather than trust it."""
    raw_path = cache_path_for(cache_dir, source)
    parsed_path = parsed_cache_path_for(cache_dir, source)
    if not raw_path.is_file() or not parsed_path.is_file():
        return None
    try:
        raw_mtime = raw_path.stat().st_mtime
        if timedelta(seconds=time.time() - raw_mtime) >= max_age:
            return None
        if parsed_path.stat().st_mtime < raw_mtime:
            return None
        with parsed_path.open("rb") as fh:
            cached_version, epg = pickle.load(fh)
    except Exception as exc:
        # Corrupt pickle, one written by a since-changed version of this
        # module (renamed/retyped field), or one written by a different
        # tvdinner version (see the version-tag note above) -- either way,
        # silently re-parse rather than let a cache artifact break EPG
        # loading or serve stale data.
        logger.warning("Discarding unreadable parsed-EPG cache for %s: %s", source, exc)
        return None
    if cached_version != __version__:
        return None
    return epg if isinstance(epg, Epg) else None


def _save_cached_parsed_epg(source: str, cache_dir: Path, epg: Epg) -> None:
    try:
        data = pickle.dumps((__version__, epg), protocol=pickle.HIGHEST_PROTOCOL)
        atomic_write_bytes(parsed_cache_path_for(cache_dir, source), data)
    except (OSError, pickle.PicklingError) as exc:
        logger.warning("Could not write parsed-EPG cache for %s: %s", source, exc)


def fetch_bytes_cached(
    source: str, cache_dir: Path, max_age: timedelta, suffix: str = ".xml", on_progress: ProgressCallback | None = None
) -> bytes | None:
    """Like _fetch_bytes, but for http(s) sources transparently caches the
    downloaded body on disk (keyed by URL) and reuses it without touching
    the network at all while younger than `max_age` -- large real-world EPG
    feeds can take tens of seconds to download, so this keeps ordinary
    startups (same feed as last time) fast. A stale cache is used as a
    fallback if the network fetch fails, rather than losing EPG data
    entirely over a transient connectivity problem. Local file/path sources
    are already fast to read and are never cached. `on_progress` is only
    ever invoked for an actual network fetch -- a cache hit is fast enough
    that it needs no progress reporting of its own."""
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme not in ("http", "https"):
        return _fetch_bytes(source)

    cache_path = cache_path_for(cache_dir, source, suffix)
    if cache_path.is_file():
        age = timedelta(seconds=time.time() - cache_path.stat().st_mtime)
        if age < max_age:
            try:
                return cache_path.read_bytes()
            except OSError:
                pass

    data = _fetch_bytes(source, on_progress=on_progress)
    if data is not None:
        try:
            atomic_write_bytes(cache_path, data)
        except OSError:
            pass
        return data

    try:
        return cache_path.read_bytes() if cache_path.is_file() else None
    except OSError:
        return None


def load_epg(
    source: str,
    cache_dir: Path | None = None,
    max_age: timedelta = DEFAULT_EPG_CACHE_MAX_AGE,
    on_progress: ProgressCallback | None = None,
) -> Epg | None:
    """Fetch and parse an XMLTV EPG document from an http(s) URL or local
    file path (transparently gzip-decompressed if needed). `cache_dir`
    enables on-disk caching of http(s) sources -- see fetch_bytes_cached
    and _load_cached_parsed_epg. `on_progress` is only ever invoked for an
    actual network fetch, never a cache hit."""
    if cache_dir:
        cached = _load_cached_parsed_epg(source, cache_dir, max_age)
        if cached is not None:
            return cached

    data = (
        fetch_bytes_cached(source, cache_dir, max_age, on_progress=on_progress)
        if cache_dir
        else _fetch_bytes(source, on_progress=on_progress)
    )
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
    on_progress: ProgressCallback | None = None,
) -> Epg | None:
    """`on_progress`, if given, applies to whichever source is currently
    being fetched -- most playlists only have one EPG source anyway (a
    comma-separated multi-source override is the exception), and the byte
    counter simply restarting for the next source if there is one is not
    worth a more elaborate per-source API for this."""
    sources = resolve_epg_sources(playlist, override)
    if not sources:
        return None

    merged = Epg()
    loaded_any = False
    for source in sources:
        epg = load_epg(source, cache_dir=cache_dir, max_age=max_age, on_progress=on_progress)
        if epg is not None:
            merged.merge(epg)
            loaded_any = True
    return merged if loaded_any else None
