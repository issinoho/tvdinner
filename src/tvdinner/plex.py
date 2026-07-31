"""Plex Media Server support.

A Plex server is addressed as a `plex://host:port?X-Plex-Token=...` URL (or
`plexs://` for an https server). Unlike xtream.py/stalker.py, this module
does not produce a Playlist/Channel -- Plex has no live-channel/EPG concept
at all, it's 100% on-demand. Instead it produces a tree of PlexNode objects
(library -> movie, or library -> show -> season -> episode) that cli.py's
Plex browser walks one level at a time via list_plex_node_children,
resolving a leaf node (a movie or episode) to a playable VodItem only once
the user actually selects it (see resolve_plex_playable) -- listing a
library or a show's children needs no extra per-item API call at all,
unlike Stalker's create_link design, since Plex's listing/children/search
endpoints already return everything needed to browse in one request per
level; only actually playing something needs the one extra per-item detail
call that resolves a file path.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

import requests

from tvdinner.vod import VodItem


@dataclass
class PlexCreds:
    base_url: str  # e.g. "http://192.168.0.218:32400", no trailing slash
    token: str


def is_plex_url(source: str) -> bool:
    return urllib.parse.urlsplit(source).scheme in ("plex", "plexs")


def parse_plex_url(source: str) -> PlexCreds | None:
    """Parse a `plex://host:port?X-Plex-Token=...` (or `plexs://` for
    https) URL. Returns None if the scheme doesn't match, the host is
    missing, or there's no token -- a malformed plex:// URL is a hard
    usage error, not something that should fall back to being treated as
    a direct stream."""
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("plex", "plexs"):
        return None
    if not parsed.hostname:
        return None

    query = urllib.parse.parse_qs(parsed.query)
    token = (query.get("X-Plex-Token") or [None])[0]
    if not token:
        return None

    scheme = "https" if parsed.scheme == "plexs" else "http"
    port = f":{parsed.port}" if parsed.port else ""
    base_url = f"{scheme}://{parsed.hostname}{port}"
    return PlexCreds(base_url=base_url, token=token)


def redact_plex_url(source: str) -> str:
    """Mask the token in a plex(s):// URL for logging/printing. Returns
    non-plex URLs (and plex URLs with no token to mask) unchanged."""
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("plex", "plexs"):
        return source

    match = re.search(r"(?:^|[?&])X-Plex-Token=([^&]+)", parsed.query, re.IGNORECASE)
    if not match:
        return source

    token = urllib.parse.unquote(match.group(1))
    redacted = f"{token[:4]}***" if len(token) > 4 else "***"
    new_query = parsed.query[: match.start(1)] + redacted + parsed.query[match.end(1) :]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


_CONTAINER_KINDS = ("library_movie", "library_show", "show", "season")


@dataclass
class PlexNode:
    """One row anywhere in a Plex server's browsable tree -- a library, a
    movie, a show, a season, or an episode. `rating_key` is the section
    `key` for a library node, or Plex's own `ratingKey` for everything
    else; either way it's what a later API call needs to fetch this
    node's children (see list_plex_node_children) or resolve it to a
    playable file (see resolve_plex_playable). `subtitle` is pre-formatted
    at fetch time (e.g. "2004 · 1h 41m", "S01E04 · 42m") so cli.py's
    renderer never needs to know Plex's field names."""

    rating_key: str
    title: str
    kind: str  # "library_movie" | "library_show" | "show" | "season" | "movie" | "episode"
    subtitle: str | None = None

    @property
    def container(self) -> bool:
        """True for a node that should be drilled into (ENTER fetches its
        children); False for a leaf node that should be resolved and
        played instead."""
        return self.kind in _CONTAINER_KINDS


class _PlexApiError(Exception):
    pass


def _headers(creds: PlexCreds) -> dict[str, str]:
    return {"Accept": "application/json", "X-Plex-Token": creds.token}


def _api_get(creds: PlexCreds, path: str, params: dict[str, str] | None = None, timeout: float = 15) -> dict:
    try:
        response = requests.get(f"{creds.base_url}{path}", params=params, headers=_headers(creds), timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _PlexApiError(f"Could not reach Plex server at {creds.base_url}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise _PlexApiError(f"Plex server at {creds.base_url} returned an unexpected response") from exc


def _dicts(value: object) -> list[dict]:
    """Filter a maybe-list (a JSON array that may contain junk, or may not
    be a list at all if the field is absent) down to just its dict
    entries -- every Plex listing endpoint below reads a `Metadata`/`Hub`
    array this way."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _format_duration(duration_ms: object) -> str | None:
    """Plex reports item length in milliseconds; render as e.g. "1h 41m"
    or "42m". None if `duration_ms` is missing or not a usable number."""
    if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
        return None
    total_minutes = round(duration_ms / 60000)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _movie_subtitle(item: dict) -> str | None:
    parts = [str(item["year"])] if item.get("year") else []
    duration = _format_duration(item.get("duration"))
    if duration:
        parts.append(duration)
    return " · ".join(parts) or None


def _show_subtitle(item: dict) -> str | None:
    return str(item["year"]) if item.get("year") else None


def _episode_subtitle(item: dict, include_show: bool = False) -> str | None:
    parts = []
    show = item.get("grandparentTitle")
    if include_show and show:
        parts.append(str(show))
    season, episode = item.get("parentIndex"), item.get("index")
    if isinstance(season, int) and isinstance(episode, int):
        parts.append(f"S{season:02d}E{episode:02d}")
    duration = _format_duration(item.get("duration"))
    if duration:
        parts.append(duration)
    return " · ".join(parts) or None


_LIBRARY_KINDS = {"movie": "library_movie", "show": "library_show"}


def list_plex_libraries(creds: PlexCreds, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """The root of the browsable tree: every movie/TV-show library on the
    server (music/photo libraries and anything else aren't playable
    video, so they're skipped). Returns (nodes, None) on success, or
    ([], message) on a hard failure -- unreachable server or invalid
    token."""
    try:
        result = _api_get(creds, "/library/sections", timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    directories = _dicts((result.get("MediaContainer") or {}).get("Directory"))
    nodes = []
    for directory in directories:
        kind = _LIBRARY_KINDS.get(directory.get("type"))
        section_key, title = directory.get("key"), directory.get("title")
        if kind is None or section_key is None or not title:
            continue
        subtitle = "Movies" if kind == "library_movie" else "TV Shows"
        nodes.append(PlexNode(rating_key=str(section_key), title=str(title), kind=kind, subtitle=subtitle))
    return nodes, None


def _list_section_items(creds: PlexCreds, section_key: str, section_kind: str, timeout: float) -> tuple[list[PlexNode], str | None]:
    try:
        result = _api_get(creds, f"/library/sections/{section_key}/all", timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    nodes = []
    for item in items:
        rating_key, title = item.get("ratingKey"), item.get("title")
        if rating_key is None or not title:
            continue
        if section_kind == "movie":
            nodes.append(PlexNode(rating_key=str(rating_key), title=str(title), kind="movie", subtitle=_movie_subtitle(item)))
        else:
            nodes.append(PlexNode(rating_key=str(rating_key), title=str(title), kind="show", subtitle=_show_subtitle(item)))
    return nodes, None


def _list_metadata_children(creds: PlexCreds, rating_key: str, child_kind: str, timeout: float) -> tuple[list[PlexNode], str | None]:
    try:
        result = _api_get(creds, f"/library/metadata/{rating_key}/children", timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    nodes = []
    for item in items:
        item_rating_key, title = item.get("ratingKey"), item.get("title")
        if item_rating_key is None or not title:
            continue
        if child_kind == "episode":
            nodes.append(
                PlexNode(rating_key=str(item_rating_key), title=str(title), kind="episode", subtitle=_episode_subtitle(item))
            )
        else:
            nodes.append(PlexNode(rating_key=str(item_rating_key), title=str(title), kind="season"))
    return nodes, None


def list_plex_node_children(creds: PlexCreds, node: PlexNode | None, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """The single dispatcher cli.py's Plex browser calls every time the
    user drills into a container node (or, with node=None, to fetch the
    root library list)."""
    if node is None:
        return list_plex_libraries(creds, timeout=timeout)
    if node.kind == "library_movie":
        return _list_section_items(creds, node.rating_key, "movie", timeout)
    if node.kind == "library_show":
        return _list_section_items(creds, node.rating_key, "show", timeout)
    if node.kind == "show":
        return _list_metadata_children(creds, node.rating_key, "season", timeout)
    if node.kind == "season":
        return _list_metadata_children(creds, node.rating_key, "episode", timeout)
    return [], f"'{node.title}' has no further items"


def resolve_plex_playable(creds: PlexCreds, node: PlexNode, timeout: float = 15) -> tuple[VodItem | None, str | None]:
    """Resolve a leaf node (a movie or episode) to a playable VodItem --
    called lazily, only on the one item the user actually selects, never
    eagerly for a whole listing. Returns (None, message) if the item has
    no playable file part at all (e.g. still being processed by Plex, or
    a metadata-only placeholder)."""
    try:
        result = _api_get(creds, f"/library/metadata/{node.rating_key}", timeout=timeout)
    except _PlexApiError as exc:
        return None, str(exc)

    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    item = items[0] if items else {}

    part_key = None
    for media in _dicts(item.get("Media")):
        for part in _dicts(media.get("Part")):
            if part.get("key"):
                part_key = part["key"]
                break
        if part_key:
            break

    if not part_key:
        return None, f"'{node.title}' has no playable file"

    url = f"{creds.base_url}{part_key}?X-Plex-Token={creds.token}"

    # thumb is a relative path (e.g. "/library/metadata/84/thumb/...") --
    # fetching it needs the same token-as-query-param treatment as the
    # file itself, since Plex requires auth for images too.
    thumb = item.get("thumb")
    poster_url = f"{creds.base_url}{thumb}?X-Plex-Token={creds.token}" if thumb else None

    audience_rating = item.get("audienceRating")
    rating = f"{audience_rating:.1f}" if isinstance(audience_rating, (int, float)) else None

    year = item.get("year")
    summary = item.get("summary")
    return (
        VodItem(
            title=node.title,
            url=url,
            poster_url=poster_url,
            year=str(year) if year else None,
            rating=rating,
            description=str(summary) if summary else None,
        ),
        None,
    )


_SEARCH_KINDS = ("movie", "show", "episode")


def search_plex(creds: PlexCreds, query: str, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """Search the whole server (every library at once) via Plex's own
    server-side search API -- confirmed live that results come back
    grouped into per-type "Hub"s (Movies/Shows/Episodes/Artists/...),
    each with a Metadata array in the same shape list_plex_node_children
    already produces. Only movie/show/episode hubs are kept; anything
    else (music, photos, actors, ...) is silently skipped, never an
    error."""
    try:
        result = _api_get(creds, "/hubs/search", params={"query": query}, timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    hubs = _dicts((result.get("MediaContainer") or {}).get("Hub"))
    nodes: list[PlexNode] = []
    for hub in hubs:
        for item in _dicts(hub.get("Metadata")):
            item_type = item.get("type")
            if item_type not in _SEARCH_KINDS:
                continue
            rating_key, title = item.get("ratingKey"), item.get("title")
            if rating_key is None or not title:
                continue
            if item_type == "movie":
                nodes.append(PlexNode(rating_key=str(rating_key), title=str(title), kind="movie", subtitle=_movie_subtitle(item)))
            elif item_type == "show":
                nodes.append(PlexNode(rating_key=str(rating_key), title=str(title), kind="show", subtitle=_show_subtitle(item)))
            else:
                nodes.append(
                    PlexNode(
                        rating_key=str(rating_key), title=str(title), kind="episode", subtitle=_episode_subtitle(item, include_show=True)
                    )
                )
    return nodes, None
