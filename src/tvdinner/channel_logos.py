"""Fallback channel logos from iptv-org's community-maintained channel/logo
database (https://github.com/iptv-org/api) -- for channels whose own
source (an M3U playlist's tvg-logo, or the loaded EPG's own <icon>, see
overlay.resolve_channel_logo) doesn't supply one at all, which many bare
M3U playlists don't.

Matching is exact only, never a fuzzy/best-guess search: a normalized
tvg_id or display-name lookup against the database's own id/name/
alt_names, reusing the exact same tvg_id/@feed-suffix and display-name
normalization tvdinner.epg already uses for its own EPG matching (see
Epg.resolve_channel_id). A miss just means no logo, same as today --
never a wrong one.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from tvdinner.epg import DEFAULT_EPG_CACHE_DIR, DEFAULT_EPG_CACHE_MAX_AGE, FEED_SUFFIX_RE, fetch_bytes_cached, normalize_name

logger = logging.getLogger(__name__)

_CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
_LOGOS_URL = "https://iptv-org.github.io/api/logos.json"


@dataclass
class OnlineLogoIndex:
    """A lookup built once (see load_online_logo_index) and reused for
    every channel that needs it -- both maps go straight from a lookup key
    to a ready-to-fetch logo URL, no further matching logic needed at
    lookup time."""

    by_id: dict[str, str] = field(default_factory=dict)
    by_name: dict[str, str] = field(default_factory=dict)

    def lookup(self, tvg_id: str | None, name: str | None = None) -> str | None:
        if tvg_id:
            if tvg_id in self.by_id:
                return self.by_id[tvg_id]
            stripped = FEED_SUFFIX_RE.sub("", tvg_id)
            if stripped != tvg_id and stripped in self.by_id:
                return self.by_id[stripped]
        if name:
            return self.by_name.get(normalize_name(name))
        return None


EMPTY_LOGO_INDEX = OnlineLogoIndex()


def _fetch_json(url: str, cache_dir: Path, max_age: timedelta) -> list | None:
    data = fetch_bytes_cached(url, cache_dir, max_age, suffix=".json")
    if data is None:
        return None
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Could not parse %s: %s", url, exc)
        return None
    return parsed if isinstance(parsed, list) else None


def load_online_logo_index(
    cache_dir: Path | None = DEFAULT_EPG_CACHE_DIR, max_age: timedelta = DEFAULT_EPG_CACHE_MAX_AGE
) -> OnlineLogoIndex:
    """Fetch (or reuse an on-disk cached copy of -- see fetch_bytes_cached,
    the same mechanism and, by default, the same cache directory and
    max_age tvdinner's own EPG loading uses) iptv-org's channel/logo
    database and build a lookup index from it. Always returns a usable
    index, never None -- on any failure (network down, cache_dir is None,
    malformed response) that's just EMPTY_LOGO_INDEX, so a caller never
    needs a None-check; a lookup against it is simply always a miss."""
    if cache_dir is None:
        return EMPTY_LOGO_INDEX

    channels = _fetch_json(_CHANNELS_URL, cache_dir, max_age)
    logos = _fetch_json(_LOGOS_URL, cache_dir, max_age)
    if channels is None or logos is None:
        return EMPTY_LOGO_INDEX

    # Multiple logo entries can exist per channel (regional/alternate
    # "feed" variants, e.g. a Plus1 or UHD version) -- prefer whichever
    # one has no feed tag at all (the primary logo for that channel).
    logo_by_channel_id: dict[str, str] = {}
    is_primary_by_channel_id: dict[str, bool] = {}
    for entry in logos:
        if not isinstance(entry, dict) or not entry.get("in_use", True):
            continue
        channel_id, url = entry.get("channel"), entry.get("url")
        if not channel_id or not url:
            continue
        is_primary = entry.get("feed") is None
        if channel_id not in logo_by_channel_id or (is_primary and not is_primary_by_channel_id[channel_id]):
            logo_by_channel_id[channel_id] = url
            is_primary_by_channel_id[channel_id] = is_primary

    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_id = channel.get("id")
        logo_url = logo_by_channel_id.get(channel_id) if channel_id else None
        if not logo_url:
            continue
        by_id[channel_id] = logo_url
        for name in (channel.get("name"), *(channel.get("alt_names") or [])):
            if not name:
                continue
            key = normalize_name(name)
            if key and key not in by_name:
                by_name[key] = logo_url

    logger.info("Loaded online logo index: %d channels with a logo", len(by_id))
    return OnlineLogoIndex(by_id=by_id, by_name=by_name)
