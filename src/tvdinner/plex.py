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

import json
import os
import platform
import re
import sys
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from tvdinner.vod import VodItem

if sys.platform == "win32":
    DEFAULT_PLEX_CLIENT_ID_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "plex_client_id.json"
else:
    DEFAULT_PLEX_CLIENT_ID_PATH = Path.home() / ".config" / "tvdinner" / "plex_client_id.json"


def load_plex_client_id(path: Path = DEFAULT_PLEX_CLIENT_ID_PATH) -> str:
    """A persisted X-Plex-Client-Identifier (see report_plex_timeline) --
    Plex needs this to stay the same across runs to treat tvdinner as one
    consistent "device" in its Now Playing/session list, rather than a
    new one every launch. Generated once via uuid.uuid4() and written to
    `path` the first time this is called; a missing/malformed file (or
    one that can't be written back, e.g. a read-only config dir) is not
    fatal -- this always returns a usable id, worst case one that just
    doesn't happen to persist this run."""
    if path.is_file():
        try:
            data = json.loads(path.read_text())
            client_id = data.get("client_id") if isinstance(data, dict) else None
            if isinstance(client_id, str) and client_id:
                return client_id
        except (OSError, json.JSONDecodeError):
            pass

    client_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"client_id": client_id}, indent=2) + "\n")
    except OSError:
        pass
    return client_id


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


_CONTAINER_KINDS = ("library_movie", "library_show", "show", "season", "continue_watching")


@dataclass
class PlexNode:
    """One row anywhere in a Plex server's browsable tree -- a library, a
    movie, a show, a season, or an episode. `rating_key` is the section
    `key` for a library node, or Plex's own `ratingKey` for everything
    else, except the synthetic "continue_watching" row (see
    list_plex_libraries), which has no real Plex key at all since it's
    not a real section -- its rating_key is never actually used, since
    list_plex_node_children dispatches on kind for it, not rating_key.
    Either way, rating_key is what a later API call needs to fetch this
    node's children (see list_plex_node_children) or resolve it to a
    playable file (see resolve_plex_playable). `subtitle` is pre-formatted
    at fetch time (e.g. "2004 · 1h 41m", "S01E04 · 42m") so cli.py's
    renderer never needs to know Plex's field names. `thumb_url` is a
    ready-to-fetch, token-authenticated image URL (see _thumb_url) --
    None if Plex has no artwork for this node, which render_plex_browser
    treats the same as a not-yet-fetched one (a plain placeholder)."""

    rating_key: str
    title: str
    kind: str  # "library_movie" | "library_show" | "continue_watching" | "show" | "season" | "movie" | "episode"
    subtitle: str | None = None
    thumb_url: str | None = None
    # Plex's own watched/in-progress status (see _leaf_watch_status/
    # _rollup_watch_status) -- never both true, and watch_progress is
    # only ever set (a 0.0-1.0 fraction) while `watched` is False.
    watched: bool = False
    watch_progress: float | None = None

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


def _thumb_url(creds: PlexCreds, item: dict) -> str | None:
    """An item's thumb/poster image URL, if it has one -- thumb (or,
    library-section Directory entries only, composite -- Plex's own
    auto-generated 4-poster collage for a section with no thumb of its
    own; movie/show/episode items never carry this field, so checking
    it unconditionally is safe) is a relative path (e.g.
    "/library/metadata/84/thumb/...") that needs the same token-as-
    query-param treatment as a playable file part, since Plex requires
    auth for images too."""
    thumb = item.get("thumb") or item.get("composite")
    return f"{creds.base_url}{thumb}?X-Plex-Token={creds.token}" if thumb else None


def _art_url(creds: PlexCreds, item: dict) -> str | None:
    """An item's wide backdrop/art image URL -- Plex's own hero-style
    background art (its `art` field), distinct from `thumb`'s portrait
    poster, same relative-path-plus-token treatment as _thumb_url. Only
    read in resolve_plex_playable (unlike thumb_url, which every
    browsable PlexNode carries): it's only ever used for the full-screen
    'i' key hero overlay (overlay.render_vod_info_overlay) once an item
    is actually resolved and playing, so there's no reason to fetch it
    for a whole listing's worth of rows nobody may ever open."""
    art = item.get("art")
    return f"{creds.base_url}{art}?X-Plex-Token={creds.token}" if art else None


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


_PLEX_PRODUCT = "tvdinner"

# platform.system()'s own value, mapped to Plex's own naming convention
# for the handful of names that differ (only Darwin, of the platforms
# tvdinner ships for) -- everything else (e.g. "Linux", "Windows")
# already matches what real Plex clients report and needs no mapping.
_PLEX_PLATFORM_NAMES = {"Darwin": "macOS"}

# The base OS (see X-Plex-Platform's own use below) -- Tautulli/Plex's
# dashboard show this as "Platform", distinct from X-Plex-Product's
# "tvdinner" app identity. Falls back to that same app identity if
# platform.system() ever returns nothing, which shouldn't happen on any
# platform tvdinner ships for, but isn't worth failing a report over.
_PLEX_PLATFORM = _PLEX_PLATFORM_NAMES.get(platform.system(), platform.system()) or _PLEX_PRODUCT

# This machine's own name (e.g. a laptop's hostname) -- X-Plex-Device-
# Name, shown as the "Player" column in Tautulli/Plex's dashboard,
# distinct from X-Plex-Device (the device *type*, e.g. "FireTV" for a
# real Fire TV client -- desktop tvdinner has no equivalent worth
# guessing at, so that one just stays _PLEX_PRODUCT, same as Product).
# Falls back to _PLEX_PRODUCT if platform.node() ever returns nothing
# (e.g. an unconfigured hostname).
_PLEX_DEVICE_NAME = platform.node() or _PLEX_PRODUCT


def report_plex_timeline(
    creds: PlexCreds,
    client_id: str,
    session_id: str,
    rating_key: str,
    state: str,
    position_seconds: float,
    duration_seconds: float,
    timeout: float = 5,
) -> tuple[bool, str | None]:
    """Tell Plex this client is playing/paused/stopped on `rating_key`,
    via the same `/:/timeline` call every real Plex client (Plex Web,
    mobile apps, Infuse, Kodi's Plex plugin, ...) uses to register a
    session -- this is what makes tvdinner's playback show up in Plex's
    own dashboard and in third-party tools like Tautulli that watch
    `/status/sessions`, and what lets Plex update its own `viewCount`/
    `viewOffset` for the item (see PlexNode.watched/watch_progress and
    VodItem.resume_seconds, which only ever *read* those fields -- this
    is the write side). `state` is one of "playing"/"paused"/"stopped";
    a "stopped" call ends the session immediately and finalizes Plex's
    own watched state whenever `position_seconds` landed.

    `client_id` (see load_plex_client_id) identifies this installation
    as one consistent device across runs; `session_id` should be a fresh
    id per distinct thing played (not per call), so repeated calls for
    the same item update one ongoing session rather than each looking
    like a new one starting.

    Never raises -- a failed report has zero effect on playback itself,
    same tolerance as every other function in this module."""
    params = {
        "ratingKey": rating_key,
        "key": f"/library/metadata/{rating_key}",
        "state": state,
        "time": str(round(position_seconds * 1000)),
        "duration": str(round(duration_seconds * 1000)),
    }
    headers = {
        **_headers(creds),
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Session-Identifier": session_id,
        "X-Plex-Product": _PLEX_PRODUCT,
        "X-Plex-Device": _PLEX_PRODUCT,
        "X-Plex-Device-Name": _PLEX_DEVICE_NAME,
        "X-Plex-Platform": _PLEX_PLATFORM,
    }
    try:
        response = requests.get(f"{creds.base_url}/:/timeline", params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        return False, f"Could not report playback state to Plex server at {creds.base_url}: {exc}"
    return True, None


def _mark_plex_watch_state(creds: PlexCreds, path: str, rating_key: str, timeout: float) -> tuple[bool, str | None]:
    """Shared body for mark_plex_watched/mark_plex_unwatched -- Plex's
    `/:/scrobble` and `/:/unscrobble` endpoints, both plain GET requests
    identified by `key` (the item's own rating_key) plus a fixed
    `identifier` naming the library plugin, same as every real Plex
    client uses for a manual "mark watched"/"mark unwatched" action.
    Works identically for a movie, episode, or show rating_key -- Plex
    itself cascades a show-level call to every episode server-side, no
    per-kind branching needed here. No response body to parse (unlike
    _api_get, which would wrongly treat Plex's empty 200 response here
    as a malformed one), so this checks only the HTTP status, same
    tolerance/return shape as report_plex_timeline."""
    params = {"key": rating_key, "identifier": "com.plexapp.plugins.library"}
    try:
        response = requests.get(f"{creds.base_url}{path}", params=params, headers=_headers(creds), timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        return False, f"Could not reach Plex server at {creds.base_url}: {exc}"
    return True, None


def mark_plex_watched(creds: PlexCreds, rating_key: str, timeout: float = 15) -> tuple[bool, str | None]:
    """Mark a movie, episode, or show (and, for a show, every episode of
    it) watched -- see _mark_plex_watch_state."""
    return _mark_plex_watch_state(creds, "/:/scrobble", rating_key, timeout)


def mark_plex_unwatched(creds: PlexCreds, rating_key: str, timeout: float = 15) -> tuple[bool, str | None]:
    """Mark a movie, episode, or show (and, for a show, every episode of
    it) unwatched -- see _mark_plex_watch_state."""
    return _mark_plex_watch_state(creds, "/:/unscrobble", rating_key, timeout)


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


def _rating_text(item: dict) -> str | None:
    """Plex's own audience score (its `rating` field is a separate
    critic score, sourced/scaled inconsistently across agents -- e.g.
    confirmed live, a Rotten-Tomatoes-sourced item's audienceRating
    isn't on the same 0-10 scale a TheMovieDb-sourced one is; not
    tvdinner's place to try to normalize that, just show what Plex
    itself reports), formatted to match every other "★ X.X" rating
    already shown elsewhere in the app (guide/VOD/history)."""
    rating = item.get("audienceRating")
    return f"★ {rating:.1f}" if isinstance(rating, (int, float)) else None


_RESOLUTION_LABELS = {"sd": "SD", "4k": "4K"}


def _resolution_badge(item: dict) -> str | None:
    """A compact quality badge (e.g. "1080p", "4K", "SD") from the
    item's first Media entry's videoResolution -- already present in
    the same listing response used to build every other PlexNode
    field, no extra per-item lookup needed. None if the item has no
    Media at all (a show/season, which has no file of its own -- only
    a movie or episode does)."""
    media = _dicts(item.get("Media"))
    if not media:
        return None
    resolution = media[0].get("videoResolution")
    if not resolution:
        return None
    return _RESOLUTION_LABELS.get(resolution, f"{resolution}p")


def _leaf_watch_status(item: dict) -> tuple[bool, float | None]:
    """(watched, watch_progress) for a movie/episode leaf item, straight
    from Plex's own viewCount/viewOffset/duration fields -- no extra
    per-item lookup needed, same reasoning as _resolution_badge above.
    Plex clears viewOffset and increments viewCount once a play is
    watched to completion, so a present, positive viewOffset always
    means "in progress" regardless of any earlier viewCount from a past
    rewatch; only otherwise does a viewCount of at least one mean
    "watched"."""
    view_offset = item.get("viewOffset")
    duration = item.get("duration")
    if isinstance(view_offset, (int, float)) and view_offset > 0 and isinstance(duration, (int, float)) and duration > 0:
        return False, min(1.0, view_offset / duration)
    view_count = item.get("viewCount")
    return isinstance(view_count, (int, float)) and view_count >= 1, None


def _rollup_watch_status(item: dict) -> tuple[bool, float | None]:
    """(watched, watch_progress) for a show/season container, from
    Plex's own leafCount/viewedLeafCount episode-count rollup -- Plex
    computes and includes these on the container's own Metadata entry
    itself, so browsing a show/season needs no per-episode lookup to
    show its aggregate watched state, same "everything needed in one
    request" reasoning as the rest of this module."""
    leaf_count = item.get("leafCount")
    viewed = item.get("viewedLeafCount")
    if not isinstance(leaf_count, (int, float)) or leaf_count <= 0 or not isinstance(viewed, (int, float)):
        return False, None
    if viewed >= leaf_count:
        return True, None
    if viewed > 0:
        return False, viewed / leaf_count
    return False, None


def _movie_subtitle(item: dict) -> str | None:
    parts = [str(item["year"])] if item.get("year") else []
    content_rating = item.get("contentRating")
    if content_rating:
        parts.append(str(content_rating))
    rating_text = _rating_text(item)
    if rating_text:
        parts.append(rating_text)
    resolution = _resolution_badge(item)
    if resolution:
        parts.append(resolution)
    duration = _format_duration(item.get("duration"))
    if duration:
        parts.append(duration)
    return " · ".join(parts) or None


def _show_subtitle(item: dict) -> str | None:
    parts = [str(item["year"])] if item.get("year") else []
    content_rating = item.get("contentRating")
    if content_rating:
        parts.append(str(content_rating))
    rating_text = _rating_text(item)
    if rating_text:
        parts.append(rating_text)
    return " · ".join(parts) or None


def _episode_subtitle(item: dict, include_show: bool = False) -> str | None:
    parts = []
    show = item.get("grandparentTitle")
    if include_show and show:
        parts.append(str(show))
    season, episode = item.get("parentIndex"), item.get("index")
    if isinstance(season, int) and isinstance(episode, int):
        parts.append(f"S{season:02d}E{episode:02d}")
    resolution = _resolution_badge(item)
    if resolution:
        parts.append(resolution)
    duration = _format_duration(item.get("duration"))
    if duration:
        parts.append(duration)
    return " · ".join(parts) or None


_LIBRARY_KINDS = {"movie": "library_movie", "show": "library_show"}


def list_plex_libraries(creds: PlexCreds, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """The root of the browsable tree: a synthetic "On Deck" row
    (see _list_on_deck) followed by every movie/TV-show library on the
    server (music/photo libraries and anything else aren't playable
    video, so they're skipped). The synthetic row always shows,
    regardless of whether anything's actually on deck right now --
    drilling into it when it's empty just gets cli.py's normal "Nothing
    found" handling, the same as any other empty listing, rather than
    this needing its own extra API call up front just to decide whether
    to show the row at all. Returns (nodes, None) on success, or
    ([], message) on a hard failure -- unreachable server or invalid
    token."""
    try:
        result = _api_get(creds, "/library/sections", timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    nodes = [
        PlexNode(
            rating_key="continue_watching",
            title="On Deck",
            kind="continue_watching",
            subtitle="In progress & up next",
        )
    ]
    directories = _dicts((result.get("MediaContainer") or {}).get("Directory"))
    for directory in directories:
        kind = _LIBRARY_KINDS.get(directory.get("type"))
        section_key, title = directory.get("key"), directory.get("title")
        if kind is None or section_key is None or not title:
            continue
        subtitle = "Movies" if kind == "library_movie" else "TV Shows"
        nodes.append(
            PlexNode(rating_key=str(section_key), title=str(title), kind=kind, subtitle=subtitle, thumb_url=_thumb_url(creds, directory))
        )
    return nodes, None


def _list_on_deck(creds: PlexCreds, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """Plex's own server-wide "On Deck" feed (see the synthetic
    root-level row list_plex_libraries prepends): movies left partway
    through, plus the next unwatched episode of any show you're
    partway through, already ranked by Plex itself (most recently
    watched first). Only ever returns movie/episode items (never a
    show itself -- Plex always resolves it to that show's specific
    next-up episode), same Metadata-array shape as every other listing
    in this module."""
    try:
        result = _api_get(creds, "/library/onDeck", timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    nodes: list[PlexNode] = []
    for item in items:
        item_type = item.get("type")
        node: PlexNode | None
        if item_type == "movie":
            node = _movie_node(creds, item)
        elif item_type == "episode":
            node = _episode_node(creds, item)
        else:
            continue
        if node is not None:
            nodes.append(node)
    return nodes, None


def _movie_node(creds: PlexCreds, item: dict) -> PlexNode | None:
    """None if the item is missing the bare minimum (ratingKey/title) to
    even show a row for -- a malformed entry gets skipped rather than
    aborting the whole listing, same tolerance as every other
    load_*/list_* function in this codebase."""
    rating_key, title = item.get("ratingKey"), item.get("title")
    if rating_key is None or not title:
        return None
    watched, watch_progress = _leaf_watch_status(item)
    return PlexNode(
        rating_key=str(rating_key),
        title=str(title),
        kind="movie",
        subtitle=_movie_subtitle(item),
        thumb_url=_thumb_url(creds, item),
        watched=watched,
        watch_progress=watch_progress,
    )


def _show_node(creds: PlexCreds, item: dict) -> PlexNode | None:
    rating_key, title = item.get("ratingKey"), item.get("title")
    if rating_key is None or not title:
        return None
    watched, watch_progress = _rollup_watch_status(item)
    return PlexNode(
        rating_key=str(rating_key),
        title=str(title),
        kind="show",
        subtitle=_show_subtitle(item),
        thumb_url=_thumb_url(creds, item),
        watched=watched,
        watch_progress=watch_progress,
    )


def _episode_node(creds: PlexCreds, item: dict) -> PlexNode | None:
    rating_key, title = item.get("ratingKey"), item.get("title")
    if rating_key is None or not title:
        return None
    watched, watch_progress = _leaf_watch_status(item)
    return PlexNode(
        rating_key=str(rating_key),
        title=str(title),
        kind="episode",
        subtitle=_episode_subtitle(item, include_show=True),
        thumb_url=_thumb_url(creds, item),
        watched=watched,
        watch_progress=watch_progress,
    )


def _list_section_items(creds: PlexCreds, section_key: str, section_kind: str, timeout: float) -> tuple[list[PlexNode], str | None]:
    try:
        result = _api_get(creds, f"/library/sections/{section_key}/all", timeout=timeout)
    except _PlexApiError as exc:
        return [], str(exc)

    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    build_node = _movie_node if section_kind == "movie" else _show_node
    nodes = [node for item in items if (node := build_node(creds, item)) is not None]
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
            watched, watch_progress = _leaf_watch_status(item)
            nodes.append(
                PlexNode(
                    rating_key=str(item_rating_key),
                    title=str(title),
                    kind="episode",
                    subtitle=_episode_subtitle(item),
                    thumb_url=_thumb_url(creds, item),
                    watched=watched,
                    watch_progress=watch_progress,
                )
            )
        else:
            watched, watch_progress = _rollup_watch_status(item)
            nodes.append(
                PlexNode(
                    rating_key=str(item_rating_key),
                    title=str(title),
                    kind="season",
                    thumb_url=_thumb_url(creds, item),
                    watched=watched,
                    watch_progress=watch_progress,
                )
            )
    return nodes, None


def list_plex_node_children(creds: PlexCreds, node: PlexNode | None, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """The single dispatcher cli.py's Plex browser calls every time the
    user drills into a container node (or, with node=None, to fetch the
    root library list)."""
    if node is None:
        return list_plex_libraries(creds, timeout=timeout)
    if node.kind == "continue_watching":
        return _list_on_deck(creds, timeout)
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

    poster_url = _thumb_url(creds, item)

    audience_rating = item.get("audienceRating")
    rating = f"{audience_rating:.1f}" if isinstance(audience_rating, (int, float)) else None

    year = item.get("year")
    summary = item.get("summary")
    # Plex's own "Director" array, when it has one -- co-directed films
    # (rare, but real) carry more than one entry, joined the same way
    # tvdinner.tmdb._fetch_movie_director joins TMDB's own crew list.
    directors = [str(d["tag"]) for d in _dicts(item.get("Director")) if d.get("tag")]
    director = ", ".join(directors) if directors else None

    # See VodItem.resume_seconds' own docstring -- same viewOffset field
    # _leaf_watch_status reads for the browser's in-progress badge, just
    # converted from Plex's milliseconds to the seconds player.play's
    # own `start` expects.
    view_offset = item.get("viewOffset")
    resume_seconds = view_offset / 1000 if isinstance(view_offset, (int, float)) and view_offset > 0 else None

    return (
        VodItem(
            title=node.title,
            url=url,
            poster_url=poster_url,
            year=str(year) if year else None,
            rating=rating,
            description=str(summary) if summary else None,
            director=director,
            backdrop_url=_art_url(creds, item),
            resume_seconds=resume_seconds,
            rating_key=node.rating_key,
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
            node: PlexNode | None
            if item_type == "movie":
                node = _movie_node(creds, item)
            elif item_type == "show":
                node = _show_node(creds, item)
            else:
                node = _episode_node(creds, item)
            if node is not None:
                nodes.append(node)
    return nodes, None


def _plex_year_sort_key(item: dict, *, is_episode: bool) -> tuple[str, str, int, int]:
    """search_plex_by_year's sort key -- grouped by Plex library first
    (librarySectionTitle, present on every /library/all result item),
    since a library only ever holds one media type (movie-only or
    TV-only), so this is what naturally separates "order alphabetically
    by film name" (a movie library) from "order by show, then season,
    then episode" (a TV library) without needing to branch on item type
    for that part. Within a TV library: a show item (matched by its own
    premiere year) sorts by its own title with season/episode both 0, so
    it lands before any of that same show's episodes (matched
    independently by air date, via grandparentTitle/parentIndex/index --
    the show name/season number/episode number Plex attaches to every
    episode result) in the rare case both appear for the same show.
    `is_episode` comes from the caller's own query type rather than this
    item's own `type` field, since that field is only actually present
    on real Plex responses -- the caller already knows unambiguously
    which of its three requests produced this item."""
    library = str(item.get("librarySectionTitle") or "").lower()
    if is_episode:
        show = str(item.get("grandparentTitle") or "").lower()
        season = item["parentIndex"] if isinstance(item.get("parentIndex"), int) else 0
        episode = item["index"] if isinstance(item.get("index"), int) else 0
        return (library, show, season, episode)
    return (library, str(item.get("title") or "").lower(), 0, 0)


def search_plex_by_year(creds: PlexCreds, year: str, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """Every movie, show, and episode across every library released in
    `year`, combined and grouped by library, movie libraries alphabetical
    by film name and TV libraries alphabetical by show/season/episode --
    see _plex_year_sort_key. Uses Plex's server-wide /library/all
    endpoint (type=1 for movies, type=2 for shows, type=4 for episodes)
    rather than querying each library section individually via
    list_plex_libraries/_list_section_items, so this is always exactly
    three requests no matter how many libraries the server has.

    Episodes need a different filter: confirmed live that an episode's
    top-level `year` field is always null (only its own
    `originallyAvailableAt` air date carries a real value, unlike a
    movie/show's `year`, which the show's *premiere* year, not
    necessarily this episode's), so Plex's plain year= filter that
    works for movies/shows silently matches nothing for episodes. An
    originallyAvailableAt range (also confirmed live) does work.

    Returns ([], message) if any of the three requests hard-fails --
    unreachable server or invalid token, the same failure this whole
    module treats as fatal everywhere else, not "no results", which
    just means no error and an empty list."""
    entries: list[tuple[tuple[str, str, int, int], PlexNode]] = []
    for media_type, build_node in ((1, _movie_node), (2, _show_node)):
        try:
            result = _api_get(creds, "/library/all", params={"type": str(media_type), "year": year}, timeout=timeout)
        except _PlexApiError as exc:
            return [], str(exc)
        items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
        entries.extend(
            (_plex_year_sort_key(item, is_episode=False), node) for item in items if (node := build_node(creds, item)) is not None
        )

    try:
        result = _api_get(
            creds,
            "/library/all",
            params={
                "type": "4",
                "originallyAvailableAt>>": f"{year}-01-01",
                "originallyAvailableAt<<": f"{year}-12-31",
            },
            timeout=timeout,
        )
    except _PlexApiError as exc:
        return [], str(exc)
    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    entries.extend(
        (_plex_year_sort_key(item, is_episode=True), node) for item in items if (node := _episode_node(creds, item)) is not None
    )

    entries.sort(key=lambda entry: entry[0])
    return [node for _, node in entries], None
