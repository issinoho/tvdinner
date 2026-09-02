"""tvtimes export-feed support.

tvtimes (https://github.com/issinoho/tvtimes) is the companion self-hosted
TV-guide web app: it aggregates several IPTV/tuner sources into one line-up
and one clock-shift-corrected XMLTV guide, then publishes both behind a
single rotatable per-account token (its Settings -> Export feeds panel).

Those two URLs are an ordinary M3U playlist and an ordinary XMLTV file, so
unlike `xtream://`/`stalker://`/`plex://` this needs no protocol module and
no `load_tvtimes_playlist` at all -- a `tvtimes://` URL is *purely sugar*
that expands to the pair, and `cli.main` rewrites it before the source
dispatch so every downstream path (M3U loader, EPG cache, guide, recording,
bookmarks) treats it as the plain M3U source it already is:

    tvtimes://tv.example.com?token=abc      ->  http://...
    tvtimess://tv.example.com?token=abc     ->  https://...

    <base>/api/exports/playlist.m3u?token=abc
    <base>/api/exports/epg.xml?token=abc

Deriving the EPG URL here rather than leaning on the playlist's own
`url-tvg=` header is deliberate: tvtimes builds that header from its
configured TVTIMES_PUBLIC_ORIGIN, which need not be the address *this*
machine reaches it on (a LAN IP vs the proxied public hostname, say). The
URL the user actually typed is the one known to work from here.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import datetime

import requests

from tvdinner.schedule import WATCHLIST_SOURCE, ScheduledRecording

_SCHEMES = ("tvtimes", "tvtimess")
# Where tvtimes mounts its token-gated export routes, under whatever base
# path it's served on (see its app.api.routers.exports).
_EXPORTS_PATH = "/api/exports"


@dataclass
class TvtimesFeed:
    base_url: str  # e.g. "https://tv.example.com" or "http://192.168.1.5:8888", no trailing slash
    token: str


def is_tvtimes_url(source: str) -> bool:
    return urllib.parse.urlsplit(source).scheme in _SCHEMES


def parse_tvtimes_url(source: str) -> TvtimesFeed | None:
    """Parse a `tvtimes://host[:port][/base/path]?token=...` URL (or
    `tvtimess://` for https).

    A path component is kept as a base path, so a tvtimes served under a
    sub-path by a reverse proxy (`tvtimess://example.com/tv?token=...`)
    resolves to `https://example.com/tv/api/exports/...`.

    Returns None if the scheme doesn't match, or the host or token is
    missing -- a malformed tvtimes:// URL is a hard usage error, not
    something to fall back to treating as a direct stream.
    """
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in _SCHEMES:
        return None
    if not parsed.hostname:
        return None

    token = (urllib.parse.parse_qs(parsed.query).get("token") or [""])[0].strip()
    if not token:
        return None

    scheme = "https" if parsed.scheme == "tvtimess" else "http"
    port = f":{parsed.port}" if parsed.port else ""
    base_path = parsed.path.rstrip("/")
    return TvtimesFeed(base_url=f"{scheme}://{parsed.hostname}{port}{base_path}", token=token)


def tvtimes_playlist_url(feed: TvtimesFeed) -> str:
    """The M3U line-up export -- what `cli.main` hands to the ordinary M3U
    loader in place of the `tvtimes://` URL."""
    return f"{feed.base_url}{_EXPORTS_PATH}/playlist.m3u?token={urllib.parse.quote(feed.token, safe='')}"


def tvtimes_epg_url(feed: TvtimesFeed) -> str:
    """The XMLTV guide export for the same account -- deterministic from the
    URL alone, no fetch needed (unlike a bare M3U's auto-discovered
    `url-tvg=`), so it can also locate a tvtimes bookmark's EPG cache file
    offline. Same role `xtream.xtream_epg_url` plays for a panel login."""
    return f"{feed.base_url}{_EXPORTS_PATH}/epg.xml?token={urllib.parse.quote(feed.token, safe='')}"


@dataclass
class WatchlistEntry:
    """One upcoming airing someone on the tvtimes account watchlisted, as
    served by its `/api/exports/watchlist.json` feed. `channel_url` is the
    same `/exports/stream/<id>?token=` URL the M3U carries, so it matches a
    loaded Channel by URL -- the key tvdinner identifies channels by."""

    channel_url: str
    channel_name: str
    title: str
    start: datetime  # tz-aware, UTC
    stop: datetime  # tz-aware, UTC


def tvtimes_watchlist_url(feed: TvtimesFeed) -> str:
    """The watchlist feed for the same account -- every upcoming airing
    anyone on it has flagged, which `cli.main --record-watchlist` turns into
    scheduled recordings."""
    return f"{feed.base_url}{_EXPORTS_PATH}/watchlist.json?token={urllib.parse.quote(feed.token, safe='')}"


def parse_watchlist(payload: object) -> list[WatchlistEntry]:
    """Parse the feed's JSON body, skipping anything malformed rather than
    raising -- one bad row from a newer/older tvtimes must not cost the whole
    poll, same tolerance as this project's other loaders."""
    if not isinstance(payload, list):
        return []
    entries: list[WatchlistEntry] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            start = datetime.fromisoformat(str(row["start"]))
            stop = datetime.fromisoformat(str(row["stop"]))
            channel_url = str(row["channel_url"])
            if not channel_url:
                continue
            entries.append(
                WatchlistEntry(
                    channel_url=channel_url,
                    channel_name=str(row.get("channel_name") or ""),
                    title=str(row.get("title") or ""),
                    start=start,
                    stop=stop,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def fetch_tvtimes_watchlist(
    feed: TvtimesFeed, timeout: float = 15
) -> tuple[list[WatchlistEntry], str | None]:
    """`(entries, error)` -- never raises. An unreachable or unparseable feed
    returns `([], message)` so the caller can log and try again on the next
    poll rather than tearing anything down."""
    url = tvtimes_watchlist_url(feed)
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        return [], f"Could not reach the tvtimes watchlist feed at {feed.base_url}: {exc}"
    try:
        payload = response.json()
    except ValueError:
        return [], f"tvtimes watchlist feed at {feed.base_url} returned a non-JSON response"
    return parse_watchlist(payload), None


def watchlist_schedule_updates(
    existing: list[ScheduledRecording], entries: list[WatchlistEntry]
) -> tuple[list[ScheduledRecording], int, int]:
    """Reconcile a watchlist feed into a schedule list, returning
    `(schedules, added, removed)`.

    Only entries marked `WATCHLIST_SOURCE` are this sync's to manage: one is
    added for every feed airing not already scheduled, and dropped again once
    it leaves the feed (un-watchlisted in tvtimes, or aired). A recording the
    user made by hand from the guide is **never** touched -- and if it already
    covers a feed airing, no duplicate is added alongside it.

    Airings are keyed by `(channel_url, start)`: the feed sends corrected UTC
    times, so this is stable across polls as long as the guide is.
    """
    wanted = {(e.channel_url, e.start): e for e in entries}
    kept: list[ScheduledRecording] = []
    covered: set[tuple[str, datetime]] = set()
    removed = 0

    for recording in existing:
        key = (recording.channel_url, recording.start)
        if recording.source != WATCHLIST_SOURCE:
            kept.append(recording)  # hand-made: not ours to reconcile
            covered.add(key)
            continue
        if key in wanted:
            kept.append(recording)
            covered.add(key)
        else:
            removed += 1

    added = 0
    for key, entry in wanted.items():
        if key in covered:
            continue
        kept.append(
            ScheduledRecording.create(
                entry.channel_url,
                entry.channel_name,
                entry.title,
                entry.start,
                entry.stop,
                source=WATCHLIST_SOURCE,
            )
        )
        added += 1
    return kept, added, removed
