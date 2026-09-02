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
