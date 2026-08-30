"""Xtream Codes IPTV panel support.

Xtream Codes is a common panel API/protocol many IPTV resellers run instead
of (or alongside) a flat M3U export. A user's login is given as a single
`xtream://username:password@host:port` URL (or `xtreams://` for an https
panel); this module resolves that into the same `Playlist`/`Channel`
objects `tvdinner.m3u.load_playlist` produces from an M3U file, so the rest
of the app (guide, favorites, EPG shifts, recording, scheduling, bookmarks)
needs no changes at all to support it.

EPG is resolved by pointing `Playlist.epg_url` at the panel's own
`xmltv.php` export -- exactly the field M3U's `x-tvg-url` header populates
-- so `tvdinner.epg` needs no changes either: `epg_channel_id` from the
live-streams API and the `id=` attribute in the panel's own XMLTV export
come from the same internal id, so `Epg.resolve_channel_id`'s exact-tvg_id
match already works.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

import requests

from tvdinner.m3u import Channel, Playlist
from tvdinner.series import SeriesNode
from tvdinner.vod import VodItem

logger = logging.getLogger(__name__)


@dataclass
class XtreamCreds:
    base_url: str  # e.g. "http://panel.example.com:8080", no trailing slash
    username: str
    password: str
    output: str = "ts"  # container extension for constructed live stream URLs


def is_xtream_url(source: str) -> bool:
    return urllib.parse.urlsplit(source).scheme in ("xtream", "xtreams")


def parse_xtream_url(source: str) -> XtreamCreds | None:
    """Parse an `xtream://user:pass@host:port` (or `xtreams://` for https)
    login URL. An optional `?output=` query param overrides the container
    extension used for constructed live stream URLs (default "ts", the
    universal safe default for Xtream panels).

    Returns None if the scheme doesn't match, or username/password/host are
    missing -- a malformed xtream:// URL is a hard usage error, not
    something that should fall back to being treated as a direct stream.
    """
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("xtream", "xtreams"):
        return None
    if not parsed.hostname or not parsed.username or not parsed.password:
        return None

    scheme = "https" if parsed.scheme == "xtreams" else "http"
    port = f":{parsed.port}" if parsed.port else ""
    base_url = f"{scheme}://{parsed.hostname}{port}"

    query = urllib.parse.parse_qs(parsed.query)
    output = (query.get("output") or ["ts"])[0].strip() or "ts"

    username = urllib.parse.unquote(parsed.username)
    password = urllib.parse.unquote(parsed.password)
    return XtreamCreds(base_url=base_url, username=username, password=password, output=output)


def redact_xtream_url(source: str) -> str:
    """Mask the password in an xtream(s):// URL for logging/printing.
    Returns non-xtream URLs (and xtream URLs with no password to mask)
    unchanged."""
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("xtream", "xtreams") or not parsed.password:
        return source

    netloc = f"{parsed.username}:***@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def xtream_epg_url(creds: XtreamCreds) -> str:
    """The panel's own xmltv.php export URL for `creds` -- deterministic
    from the login alone, no API call needed (unlike a bare M3U's
    auto-discovered EPG URL, which requires actually fetching the
    playlist first). Used both to populate Playlist.epg_url below and,
    offline, by cli.py's stats command to locate an Xtream bookmark's EPG
    cache file without re-logging into the panel."""
    return f"{creds.base_url}/xmltv.php?username={creds.username}&password={creds.password}"


class _XtreamApiError(Exception):
    pass


def _api_get(creds: XtreamCreds, action: str | None, timeout: float, **extra_params: str) -> dict | list:
    params = {"username": creds.username, "password": creds.password}
    if action:
        params["action"] = action
    params.update(extra_params)
    try:
        response = requests.get(f"{creds.base_url}/player_api.php", params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _XtreamApiError(f"Could not reach Xtream server at {creds.base_url}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise _XtreamApiError(f"Xtream server at {creds.base_url} returned an unexpected response") from exc


def load_xtream_playlist(creds: XtreamCreds, timeout: float = 15) -> tuple[Playlist | None, str | None]:
    """Log into an Xtream panel and build a Playlist from its live channels,
    with `epg_url` pointed at the panel's own XMLTV export. Returns
    (playlist, None) on success, or (None, message) on a hard failure
    (unreachable server, or invalid credentials) -- the caller should
    surface `message` and not attempt to play the xtream:// URL as a raw
    stream.
    """
    try:
        handshake = _api_get(creds, None, timeout)
    except _XtreamApiError as exc:
        return None, str(exc)

    user_info = handshake.get("user_info") if isinstance(handshake, dict) else None
    if not isinstance(user_info, dict) or not user_info.get("auth"):
        return None, "Invalid Xtream username or password"

    status = user_info.get("status")
    if status and status != "Active":
        logger.warning("Xtream account status is %r (expected 'Active')", status)

    try:
        categories_raw = _api_get(creds, "get_live_categories", timeout)
        streams_raw = _api_get(creds, "get_live_streams", timeout)
    except _XtreamApiError as exc:
        return None, str(exc)

    categories = {
        str(category["category_id"]): category.get("category_name", "")
        for category in categories_raw
        if isinstance(category, dict) and category.get("category_id") is not None
    } if isinstance(categories_raw, list) else {}

    channels: list[Channel] = []
    for stream in streams_raw if isinstance(streams_raw, list) else []:
        if not isinstance(stream, dict):
            continue
        stream_id = stream.get("stream_id")
        name = stream.get("name")
        if stream_id is None or not name:
            continue

        category_ids = stream.get("category_ids")
        if isinstance(category_ids, list) and category_ids:
            ids = [str(category_id) for category_id in category_ids]
        elif stream.get("category_id") is not None:
            ids = [str(stream["category_id"])]
        else:
            ids = []
        group_title = ";".join(categories[i] for i in ids if categories.get(i)) or None

        epg_id = stream.get("epg_channel_id") or None
        channels.append(
            Channel(
                name=str(name),
                url=f"{creds.base_url}/live/{creds.username}/{creds.password}/{stream_id}.{creds.output}",
                tvg_id=str(epg_id) if epg_id else None,
                tvg_logo=stream.get("stream_icon") or None,
                group_title=group_title,
            )
        )

    return Playlist(channels=channels, epg_url=xtream_epg_url(creds)), None


def load_xtream_vod(creds: XtreamCreds, timeout: float = 15) -> tuple[list[VodItem], str | None]:
    """Log into an Xtream panel and build a list of VodItems from its VOD
    (movies) library. Returns (items, None) on success, or ([], message) on
    a hard failure -- unlike load_xtream_playlist, this is meant to be
    treated as non-fatal by the caller (VOD is supplementary to live TV,
    not the primary use case), so an empty list plus a warning is enough.

    Xtream VOD stream URLs are deterministic (same shape as live stream
    URLs), so unlike Stalker there's no per-item resolve call needed."""
    try:
        handshake = _api_get(creds, None, timeout)
    except _XtreamApiError as exc:
        return [], str(exc)

    user_info = handshake.get("user_info") if isinstance(handshake, dict) else None
    if not isinstance(user_info, dict) or not user_info.get("auth"):
        return [], "Invalid Xtream username or password"

    try:
        categories_raw = _api_get(creds, "get_vod_categories", timeout)
        streams_raw = _api_get(creds, "get_vod_streams", timeout)
    except _XtreamApiError as exc:
        return [], str(exc)

    categories = {
        str(category["category_id"]): category.get("category_name", "")
        for category in categories_raw
        if isinstance(category, dict) and category.get("category_id") is not None
    } if isinstance(categories_raw, list) else {}

    items: list[VodItem] = []
    for stream in streams_raw if isinstance(streams_raw, list) else []:
        if not isinstance(stream, dict):
            continue
        stream_id = stream.get("stream_id")
        name = stream.get("name")
        if stream_id is None or not name:
            continue

        category_id = stream.get("category_id")
        group_title = categories.get(str(category_id)) if category_id is not None else None

        extension = stream.get("container_extension") or "mp4"
        rating = stream.get("rating")
        items.append(
            VodItem(
                title=str(name),
                url=f"{creds.base_url}/movie/{creds.username}/{creds.password}/{stream_id}.{extension}",
                group_title=group_title or None,
                poster_url=stream.get("stream_icon") or None,
                rating=str(rating) if rating not in (None, "", "0") else None,
            )
        )

    return items, None


def _fetch_series_info(
    creds: XtreamCreds, series_id: str, timeout: float
) -> tuple[dict[int, str | None], dict[int, list[dict]], str | None]:
    """Shared by list_xtream_series_children's "series" and "season"
    branches below -- both need the same get_series_info&series_id=<id>
    call (drilling series -> season -> episode costs two near-identical
    fetches of this, since the response isn't cached between them --
    accepted v1 cost, same as Plex's own per-drill-level call cost).
    Returns ({season_number: season_poster_url}, {season_number: [raw
    episode dict, ...]}, None) on success, or ({}, {}, message) on
    failure."""
    try:
        info = _api_get(creds, "get_series_info", timeout, series_id=series_id)
    except _XtreamApiError as exc:
        return {}, {}, str(exc)
    if not isinstance(info, dict):
        return {}, {}, "Xtream server returned an unexpected response for this series"

    seasons: dict[int, str | None] = {}
    for season in info.get("seasons") if isinstance(info.get("seasons"), list) else []:
        if not isinstance(season, dict):
            continue
        season_number = season.get("season_number")
        if not isinstance(season_number, int):
            continue
        seasons[season_number] = season.get("cover") or season.get("cover_big") or None

    episodes_by_season: dict[int, list[dict]] = {}
    raw_episodes = info.get("episodes")
    if isinstance(raw_episodes, dict):
        for season_key, episode_list in raw_episodes.items():
            try:
                season_number = int(season_key)
            except (TypeError, ValueError):
                continue
            if isinstance(episode_list, list):
                episodes_by_season[season_number] = [episode for episode in episode_list if isinstance(episode, dict)]

    return seasons, episodes_by_season, None


def list_xtream_series_children(
    creds: XtreamCreds, node: SeriesNode | None, timeout: float = 15
) -> tuple[list[SeriesNode], str | None]:
    """One dispatcher, mirroring plex.list_plex_node_children: `node` is
    None for the root (series categories, via get_series_categories), a
    "category" node lists the series within it (get_series&category_id=),
    a "series" node lists its seasons, and a "season" node lists that
    season's episodes -- the latter two both via get_series_info (see
    _fetch_series_info above), since Xtream returns a whole series'
    season/episode tree in one call rather than one call per season.

    Each episode's `url` is already built when listed: Xtream series
    stream URLs are deterministic (same "credentials in the path" shape
    as a VOD movie's URL, see load_xtream_vod).

    FLAG: the /series/{user}/{pass}/{episode_id}.{ext} URL convention
    below is inferred by analogy with the confirmed /movie/... and
    /live/... shapes elsewhere in this module -- not independently
    verified against a live panel. Verify against a real panel before
    trusting playback.

    Returns (nodes, None) on success, or ([], message) on a hard
    failure -- meant to be treated as non-fatal by the caller, same as
    load_xtream_vod."""
    if node is None:
        try:
            handshake = _api_get(creds, None, timeout)
        except _XtreamApiError as exc:
            return [], str(exc)
        user_info = handshake.get("user_info") if isinstance(handshake, dict) else None
        if not isinstance(user_info, dict) or not user_info.get("auth"):
            return [], "Invalid Xtream username or password"

        try:
            categories_raw = _api_get(creds, "get_series_categories", timeout)
        except _XtreamApiError as exc:
            return [], str(exc)

        nodes: list[SeriesNode] = []
        for category in categories_raw if isinstance(categories_raw, list) else []:
            if not isinstance(category, dict):
                continue
            category_id = category.get("category_id")
            name = category.get("category_name")
            if category_id is None or not name:
                continue
            nodes.append(SeriesNode(id=str(category_id), title=str(name), kind="category"))
        return nodes, None

    if node.kind == "category":
        try:
            series_raw = _api_get(creds, "get_series", timeout, category_id=node.id)
        except _XtreamApiError as exc:
            return [], str(exc)

        nodes = []
        for series in series_raw if isinstance(series_raw, list) else []:
            if not isinstance(series, dict):
                continue
            series_id = series.get("series_id")
            name = series.get("name")
            if series_id is None or not name:
                continue
            rating = series.get("rating")
            release_date = series.get("releaseDate") or series.get("release_date")
            nodes.append(
                SeriesNode(
                    id=str(series_id),
                    title=str(name),
                    kind="series",
                    poster_url=series.get("cover") or None,
                    year=str(release_date)[:4] if release_date else None,
                    rating=str(rating) if rating not in (None, "", "0") else None,
                )
            )
        return nodes, None

    if node.kind == "series":
        seasons, episodes_by_season, error = _fetch_series_info(creds, node.id, timeout)
        if error:
            return [], error

        nodes = []
        for season_number in sorted(episodes_by_season):
            episode_count = len(episodes_by_season[season_number])
            nodes.append(
                SeriesNode(
                    id=f"{node.id}:{season_number}",
                    title=f"Season {season_number}",
                    kind="season",
                    poster_url=seasons.get(season_number) or node.poster_url,
                    season_number=season_number,
                    series_title=node.title,
                    subtitle=f"{episode_count} episode{'s' if episode_count != 1 else ''}",
                )
            )
        return nodes, None

    if node.kind == "season":
        series_id, _, season_part = node.id.partition(":")
        try:
            season_number = int(season_part)
        except ValueError:
            return [], f"Malformed season id {node.id!r}"

        _seasons, episodes_by_season, error = _fetch_series_info(creds, series_id, timeout)
        if error:
            return [], error

        nodes = []
        for episode in episodes_by_season.get(season_number, []):
            episode_id = episode.get("id")
            title = episode.get("title")
            if episode_id is None or not title:
                continue
            episode_num_raw = episode.get("episode_num")
            episode_num = int(episode_num_raw) if isinstance(episode_num_raw, int) or (
                isinstance(episode_num_raw, str) and episode_num_raw.isdigit()
            ) else None
            extension = episode.get("container_extension") or "mp4"
            nodes.append(
                SeriesNode(
                    id=str(episode_id),
                    title=str(title),
                    kind="episode",
                    season_number=season_number,
                    episode_number=episode_num,
                    series_title=node.series_title,
                    subtitle=f"S{season_number:02d}E{episode_num:02d}" if episode_num is not None else None,
                    url=f"{creds.base_url}/series/{creds.username}/{creds.password}/{episode_id}.{extension}",
                )
            )
        return nodes, None

    return [], f"'{node.title}' has no further items"


def resolve_xtream_series_episode(
    creds: XtreamCreds, node: SeriesNode, timeout: float = 15
) -> tuple[VodItem | None, str | None]:
    """Builds a VodItem straight from `node`'s own fields -- no network
    call needed, since an Xtream episode's playable URL is already
    deterministic and was set on node.url when list_xtream_series_children
    listed it (see there). `creds`/`timeout` are accepted but unused,
    purely so cli.py's leaf-select code path stays uniform with a future
    source whose resolve step does need a real network call."""
    del creds, timeout
    if not node.url:
        return None, f"'{node.title}' has no playable URL"
    return (
        VodItem(
            title=node.title,
            url=node.url,
            year=node.year,
            rating=node.rating,
            series_title=node.series_title,
            season_number=node.season_number,
            episode_number=node.episode_number,
        ),
        None,
    )
