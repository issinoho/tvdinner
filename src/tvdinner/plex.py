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

from tvdinner.vod import VodChapter, VodItem, VodMarker

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
    # A movie/show/episode's own release/air year -- only ever set by
    # _movie_node/_show_node/_episode_node (the "year" field
    # _movie_subtitle/_show_subtitle already read, just never exposed
    # structurally before). Used by cli.py's TMDB title-logo lookup
    # (tmdb.fetch_movie_logo_cached/fetch_tv_logo_cached), which needs it
    # separately from `subtitle`'s already-formatted display text.
    year: str | None = None
    # An episode's own season poster (Plex's `parentThumb`) -- only ever
    # set by _episode_node, straight from that one episode's own
    # metadata, regardless of which listing it was fetched as part of
    # (a season's own episode list, or Continue Watching's flat on-deck
    # one, which has no season/show frame in between to fall back
    # through -- see overlay._plex_selected_poster's own docstring for
    # why an episode's own screengrab shouldn't be the backdrop).
    season_thumb_url: str | None = None
    # An episode's own show name (Plex's `grandparentTitle`) -- same
    # "straight from this one episode's own metadata, regardless of
    # listing context" reasoning as season_thumb_url above. Mirrors
    # vod.VodItem.series_title's identical purpose for Plex playback;
    # this one is for cli.py's Plex *browser* backdrop instead (see
    # _plex_title_logo_target).
    series_title: str | None = None
    # An episode's own show *id* (Plex's `grandparentRatingKey`) -- same
    # "straight from this one episode's own metadata, regardless of
    # listing context" reasoning as series_title/season_thumb_url above,
    # but a real, usable rating_key rather than just display text.
    # _plex_title_logo_target reads this directly for an episode
    # selection instead of walking the nav stack outward looking for a
    # real show ancestor frame -- confirmed live that the walk-outward
    # approach picks up whatever unrelated show happens to be selected
    # in whatever frame is sitting underneath a flat episode listing
    # (search results, Continue Watching's on-deck list) in the nav
    # stack, which is only ever a real ancestor by coincidence, not by
    # construction, for anything that isn't a show's own season/episode
    # drill-down.
    grandparent_rating_key: str | None = None

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


def _relative_image_url(creds: PlexCreds, path: str | None) -> str | None:
    """A Plex-relative image path (e.g. "/library/metadata/84/thumb/...")
    resolved to a ready-to-fetch, token-authenticated URL -- shared by
    _thumb_url/_art_url and _episode_node's own parentThumb read, since
    Plex requires the same token-as-query-param auth for every image."""
    return f"{creds.base_url}{path}?X-Plex-Token={creds.token}" if path else None


def _thumb_url(creds: PlexCreds, item: dict) -> str | None:
    """An item's thumb/poster image URL, if it has one -- thumb (or,
    library-section Directory entries only, composite -- Plex's own
    auto-generated 4-poster collage for a section with no thumb of its
    own; movie/show/episode items never carry this field, so checking
    it unconditionally is safe)."""
    return _relative_image_url(creds, item.get("thumb") or item.get("composite"))


def _art_url(creds: PlexCreds, item: dict) -> str | None:
    """An item's wide backdrop/art image URL -- Plex's own hero-style
    background art (its `art` field), distinct from `thumb`'s portrait
    poster. Only read in resolve_plex_playable (unlike thumb_url, which
    every browsable PlexNode carries): it's only ever used for the
    full-screen 'i' key hero overlay (overlay.render_vod_info_overlay)
    once an item is actually resolved and playing, so there's no reason
    to fetch it for a whole listing's worth of rows nobody may ever
    open."""
    return _relative_image_url(creds, item.get("art"))


def plex_theme_url(creds: PlexCreds, rating_key: str) -> str:
    """A show's theme-music URL -- .../library/metadata/{rating_key}/theme,
    same token-as-query-param auth as every other Plex asset URL (see
    _relative_image_url). An MP3, or empty/404 if the show's metadata
    has none (depends on which agent originally scraped it) -- never
    checked here, left to whatever plays it to fail silently. Public
    (unlike _relative_image_url/_thumb_url/_art_url) since cli.py builds
    this straight from a rating_key it already has, not from an API
    response dict the way every other image URL above is."""
    return f"{creds.base_url}/library/metadata/{rating_key}/theme?X-Plex-Token={creds.token}"


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
    year = item.get("year")
    return PlexNode(
        rating_key=str(rating_key),
        title=str(title),
        kind="movie",
        subtitle=_movie_subtitle(item),
        thumb_url=_thumb_url(creds, item),
        watched=watched,
        watch_progress=watch_progress,
        year=str(year) if year else None,
    )


def _show_node(creds: PlexCreds, item: dict) -> PlexNode | None:
    rating_key, title = item.get("ratingKey"), item.get("title")
    if rating_key is None or not title:
        return None
    watched, watch_progress = _rollup_watch_status(item)
    year = item.get("year")
    return PlexNode(
        rating_key=str(rating_key),
        title=str(title),
        kind="show",
        subtitle=_show_subtitle(item),
        thumb_url=_thumb_url(creds, item),
        watched=watched,
        watch_progress=watch_progress,
        year=str(year) if year else None,
    )


def _episode_node(creds: PlexCreds, item: dict) -> PlexNode | None:
    rating_key, title = item.get("ratingKey"), item.get("title")
    if rating_key is None or not title:
        return None
    watched, watch_progress = _leaf_watch_status(item)
    year = item.get("year")
    show = item.get("grandparentTitle")
    show_rating_key = item.get("grandparentRatingKey")
    return PlexNode(
        rating_key=str(rating_key),
        title=str(title),
        kind="episode",
        subtitle=_episode_subtitle(item, include_show=True),
        thumb_url=_thumb_url(creds, item),
        watched=watched,
        watch_progress=watch_progress,
        year=str(year) if year else None,
        season_thumb_url=_relative_image_url(creds, item.get("parentThumb")),
        series_title=str(show) if show else None,
        grandparent_rating_key=str(show_rating_key) if show_rating_key else None,
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
            year = item.get("year")
            show = item.get("grandparentTitle")
            show_rating_key = item.get("grandparentRatingKey")
            nodes.append(
                PlexNode(
                    rating_key=str(item_rating_key),
                    title=str(title),
                    kind="episode",
                    subtitle=_episode_subtitle(item),
                    thumb_url=_thumb_url(creds, item),
                    watched=watched,
                    watch_progress=watch_progress,
                    year=str(year) if year else None,
                    season_thumb_url=_relative_image_url(creds, item.get("parentThumb")),
                    series_title=str(show) if show else None,
                    grandparent_rating_key=str(show_rating_key) if show_rating_key else None,
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


def _chapters(creds: PlexCreds, item: dict) -> list[VodChapter] | None:
    """Plex's own `Chapter` metadata array, when the source file has real
    embedded chapter markers (e.g. a Blu-ray/DVD rip) -- present in the
    same /library/metadata/{id} response resolve_plex_playable already
    fetches, no extra request needed. `tag` is the chapter's own title,
    when Plex/the source has one (often absent -- a bare "Chapter 3" is
    Plex's own client-side fallback label, not something the API
    actually returns). `thumb`, when present, is a real server-generated
    chapter-thumbnail image (not guaranteed even for chaptered media --
    see VodChapter.thumb_url's own docstring for the fallback), resolved
    through the same token-authenticated _relative_image_url helper
    _thumb_url/_art_url already use. Sorted by startTimeOffset since
    Plex's own ordering isn't documented as guaranteed. None (not an
    empty list) when the item has no Chapter array at all, so overlay.py
    can tell "no chapters" apart from "chapters array was empty for some
    reason" -- not that the distinction currently matters, but it
    mirrors resume_seconds/rating_key's own None-means-absent
    convention."""
    entries = _dicts(item.get("Chapter"))
    if not entries:
        return None
    chapters = [
        VodChapter(
            start_seconds=entry["startTimeOffset"] / 1000,
            title=str(entry["tag"]) if entry.get("tag") else None,
            thumb_url=_relative_image_url(creds, entry.get("thumb")),
        )
        for entry in entries
        if isinstance(entry.get("startTimeOffset"), (int, float))
    ]
    chapters.sort(key=lambda c: c.start_seconds)
    return chapters or None


def _markers(item: dict) -> tuple[VodMarker | None, VodMarker | None]:
    """Plex's own `Marker` metadata array -- a Plex Pass feature, present
    in the same /library/metadata/{id} response resolve_plex_playable
    already fetches (see includeMarkers=1 there), only once the library's
    intro/credits detection has actually analyzed the item (most items
    have none even on a Plex Pass server). Returns (intro, credits): the
    earliest entry of each `type`, converted from Plex's milliseconds to
    seconds, or None for either/both when absent. Doesn't handle Plex's
    multi-credits-marker `final` flag (a mid-credits scene marker vs. the
    true end) -- just the first `credits` entry by start time, a known
    v1 simplification, not a bug."""
    entries = sorted(
        (
            entry
            for entry in _dicts(item.get("Marker"))
            if entry.get("type") in ("intro", "credits")
            and isinstance(entry.get("startTimeOffset"), (int, float))
            and isinstance(entry.get("endTimeOffset"), (int, float))
        ),
        key=lambda entry: entry["startTimeOffset"],
    )
    intro = next((entry for entry in entries if entry["type"] == "intro"), None)
    credits_entry = next((entry for entry in entries if entry["type"] == "credits"), None)
    return (
        VodMarker(start_seconds=intro["startTimeOffset"] / 1000, end_seconds=intro["endTimeOffset"] / 1000)
        if intro
        else None,
        VodMarker(start_seconds=credits_entry["startTimeOffset"] / 1000, end_seconds=credits_entry["endTimeOffset"] / 1000)
        if credits_entry
        else None,
    )


def _tmdb_guid_id(item: dict) -> int | None:
    """The numeric id out of a Plex metadata item's own Guid array entry
    for TMDB (`{"id": "tmdb://1418"}`), if it has one. This is Plex's
    own metadata, present only on the detailed per-item endpoint (not a
    library listing) -- confirmed live against a real server -- so no
    --tmdb-api-token or TMDB API call is involved here at all."""
    for guid in _dicts(item.get("Guid")):
        guid_id = str(guid.get("id") or "")
        if guid_id.startswith("tmdb://"):
            try:
                return int(guid_id.removeprefix("tmdb://"))
            except ValueError:
                return None
    return None


def resolve_plex_playable(creds: PlexCreds, node: PlexNode, timeout: float = 15) -> tuple[VodItem | None, str | None]:
    """Resolve a leaf node (a movie or episode) to a playable VodItem --
    called lazily, only on the one item the user actually selects, never
    eagerly for a whole listing. Returns (None, message) if the item has
    no playable file part at all (e.g. still being processed by Plex, or
    a metadata-only placeholder)."""
    try:
        # includeChapters=1 is required -- confirmed live that Plex omits
        # the Chapter array entirely without it, even for an item whose
        # own chapterSource field (present either way) proves it has real
        # chapter data. includeMarkers=1 is the equivalent for the
        # intro/credits Marker array (see _markers) -- unconfirmed live
        # (no title on hand with real marker data at the time this was
        # added), but the same param shape as includeChapters, and a
        # harmless no-op if Plex ignores it or the item has no markers.
        result = _api_get(
            creds,
            f"/library/metadata/{node.rating_key}",
            params={"includeChapters": "1", "includeMarkers": "1"},
            timeout=timeout,
        )
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
    # The show's own name, present on an episode item (absent on a
    # movie item) -- see VodItem.series_title's own docstring.
    show = item.get("grandparentTitle")
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
    intro_marker, credits_marker = _markers(item)
    # Only ever present on an episode item (Plex's TV hierarchy), same as
    # grandparentTitle above -- absent for a movie, so plex_parent_rating_key
    # naturally ends up unset for one without any extra branching here.
    parent_rating_key = item.get("parentRatingKey")
    grandparent_rating_key = item.get("grandparentRatingKey")
    # Only meaningful for a movie -- an episode's own Guid is an
    # episode-level TMDB id, not the show-level id a "view on TMDB" link
    # needs, so it's left unset here and resolved separately (see
    # cli.py's plex_show_tmdb_ids, keyed by grandparent_rating_key above).
    # node.kind (the caller's own, already-known classification), not
    # this response's own "type" field -- resolve_plex_playable is only
    # ever called on a node already known to be a movie or an episode
    # (see PlexNode.container/list_plex_node_children), so node.kind is
    # just as reliable here and doesn't add a new dependency on a field
    # this function otherwise never reads.
    tmdb_id = _tmdb_guid_id(item) if node.kind == "movie" else None

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
            series_title=str(show) if show else None,
            chapters=_chapters(creds, item),
            intro_marker=intro_marker,
            credits_marker=credits_marker,
            plex_parent_rating_key=str(parent_rating_key) if parent_rating_key else None,
            plex_grandparent_rating_key=str(grandparent_rating_key) if grandparent_rating_key else None,
            tmdb_id=tmdb_id,
        ),
        None,
    )


def show_tmdb_id(creds: PlexCreds, rating_key: str, timeout: float = 15) -> int | None:
    """The show's own TMDB id (Plex's Guid tmdb:// entry on the show's
    own metadata), for cli.py's "view on TMDB" action when the item
    currently on screen is an episode -- resolve_plex_playable only
    fetches the episode's own metadata, whose Guid is a different,
    episode-level id (confirmed live against a real server), not a
    /tv/{id}-linkable show id. A separate, minimal fetch by design,
    mirroring find_next_episode's shape, rather than reusing the
    heavier resolve_plex_playable for a single field."""
    try:
        result = _api_get(creds, f"/library/metadata/{rating_key}", timeout=timeout)
    except _PlexApiError:
        return None
    items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
    return _tmdb_guid_id(items[0]) if items else None


def find_next_episode(
    creds: PlexCreds, rating_key: str, parent_rating_key: str, grandparent_rating_key: str | None, timeout: float = 15
) -> PlexNode | None:
    """The episode right after `rating_key` in its own season
    (`parent_rating_key`), for cli.py's end-of-episode "Up Next" prompt.
    Reuses _list_metadata_children -- the same call the season/show
    browser already makes -- rather than a dedicated endpoint: Plex
    returns a season's episodes (or a show's seasons) in on-screen order
    already, confirmed live against a real server, so finding "next" is
    just finding `rating_key` in that list and taking the following
    entry. Falls through to the next *season* (via `grandparent_rating_key`,
    the show) the same way when `rating_key` was the season's last
    episode, and returns the first episode of the season after that.
    None at any dead end -- the last episode of the whole show, a lookup
    error, or a movie with no grandparent_rating_key to fall through
    with -- same "fails safe, doesn't raise" convention as the rest of
    this module."""
    episodes, _ = _list_metadata_children(creds, parent_rating_key, "episode", timeout)
    positions = [index for index, episode in enumerate(episodes) if episode.rating_key == rating_key]
    if positions and positions[0] + 1 < len(episodes):
        return episodes[positions[0] + 1]

    if not grandparent_rating_key:
        return None
    seasons, _ = _list_metadata_children(creds, grandparent_rating_key, "season", timeout)
    season_positions = [index for index, season in enumerate(seasons) if season.rating_key == parent_rating_key]
    if not season_positions or season_positions[0] + 1 >= len(seasons):
        return None
    next_season = seasons[season_positions[0] + 1]
    next_season_episodes, _ = _list_metadata_children(creds, next_season.rating_key, "episode", timeout)
    return next_season_episodes[0] if next_season_episodes else None


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


def _plex_year_sort_key(item: dict, *, category: str) -> tuple[int, str, int, int]:
    """search_plex_by_year's sort key -- every movie library on the
    server treated as one big virtual library (alphabetical by film
    name), same for every TV library (alphabetical by show, then
    numerically by season/episode), regardless of which actual library
    each item came from. `category` ("movie"/"show"/"episode") comes
    from the caller's own query rather than this item's own `type`
    field, since that field is only actually present on real Plex
    responses -- the caller already knows unambiguously which of its
    three requests produced this item. Movies always sort before TV
    content (bucket 0 vs 1) -- there's no meaningful way to interleave
    a film title with a show name, so this just keeps the two kinds
    from mixing arbitrarily. Within TV: a show item (matched by its own
    premiere year) sorts by its own title with season/episode both 0,
    so it lands before any of that same show's episodes (matched
    independently by air date, via grandparentTitle/parentIndex/index --
    the show name/season number/episode number Plex attaches to every
    episode result) in the rare case both appear for the same show."""
    if category == "episode":
        show = str(item.get("grandparentTitle") or "").lower()
        season = item["parentIndex"] if isinstance(item.get("parentIndex"), int) else 0
        episode = item["index"] if isinstance(item.get("index"), int) else 0
        return (1, show, season, episode)
    bucket = 0 if category == "movie" else 1
    return (bucket, str(item.get("title") or "").lower(), 0, 0)


def search_plex_by_year(creds: PlexCreds, year: str, timeout: float = 15) -> tuple[list[PlexNode], str | None]:
    """Every movie, show, and episode across every library released in
    `year`, combined as if every movie library were one big virtual
    library (alphabetical by film name) and every TV library were
    another (alphabetical by show, then numerically by season/episode)
    -- see _plex_year_sort_key. Uses Plex's server-wide /library/all
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
    entries: list[tuple[tuple[int, str, int, int], PlexNode]] = []
    for media_type, build_node, category in ((1, _movie_node, "movie"), (2, _show_node, "show")):
        try:
            result = _api_get(creds, "/library/all", params={"type": str(media_type), "year": year}, timeout=timeout)
        except _PlexApiError as exc:
            return [], str(exc)
        items = _dicts((result.get("MediaContainer") or {}).get("Metadata"))
        entries.extend(
            (_plex_year_sort_key(item, category=category), node)
            for item in items
            if (node := build_node(creds, item)) is not None
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
        (_plex_year_sort_key(item, category="episode"), node)
        for item in items
        if (node := _episode_node(creds, item)) is not None
    )

    entries.sort(key=lambda entry: entry[0])
    return [node for _, node in entries], None
