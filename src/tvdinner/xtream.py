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


class _XtreamApiError(Exception):
    pass


def _api_get(creds: XtreamCreds, action: str | None, timeout: float) -> dict | list:
    params = {"username": creds.username, "password": creds.password}
    if action:
        params["action"] = action
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

    epg_url = f"{creds.base_url}/xmltv.php?username={creds.username}&password={creds.password}"
    return Playlist(channels=channels, epg_url=epg_url), None
