"""Stalker Portal (Ministra) IPTV middleware support.

Stalker Portal -- also known as Ministra, or "Stalker Middleware" -- is the
protocol MAG25x/26x set-top boxes speak. It has no official spec; the
handshake/get_profile/get_genres/get_all_channels/create_link flow below is
the widely-observed behavior real Stalker clients rely on, reverse
engineered by the community.

Unlike an Xtream Codes login (see tvdinner.xtream), a Stalker portal has no
username/password -- a whitelisted MAC address is the entire access
credential -- and a channel has no static playable URL: its "cmd" field
must be exchanged for a real stream URL via a create_link call. This module
resolves that once per channel at load time and stores the result as
Channel.url, exactly like tvdinner.xtream does, so the rest of the app
needs no changes to support it. There is deliberately no EPG support here:
most Stalker deployments don't expose one bulk XMLTV-style export the way
an Xtream panel's xmltv.php almost always does, so a Stalker Playlist is
built with no epg_url, and behaves like any other EPG-less playlist.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from tvdinner.m3u import Channel, Playlist
from tvdinner.vod import VodItem

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# A bounded pool for the per-channel create_link calls -- serial would be
# far too slow for a few hundred channels, unbounded risks tripping a
# portal's rate limiting.
_CREATE_LINK_WORKERS = 8

_USER_AGENT = (
    "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) "
    "MAG200 stbapp ver: 2 rev: 250 Safari/533.3"
)


@dataclass
class StalkerCreds:
    base_url: str  # e.g. "http://panel.example.com:8080", no trailing slash
    portal_path: str  # e.g. "/stalker_portal/c/portal.php", taken verbatim from the URL's path
    mac: str
    serial: str | None = None
    device_id: str | None = None
    stb_type: str = "MAG250"


def is_stalker_url(source: str) -> bool:
    return urllib.parse.urlsplit(source).scheme in ("stalker", "stalkers")


def parse_stalker_url(source: str) -> StalkerCreds | None:
    """Parse a `stalker://host:port/portal/path?mac=AA:BB:CC:DD:EE:FF`
    login URL (`stalkers://` for https). A MAC's colons are fine unencoded
    in the query component (unlike Xtream's user:pass, which needs the
    URL's userinfo section), so the MAC is given as a query param rather
    than packed into the authority.

    The path is taken verbatim as the portal path; if it doesn't already
    end in ".php", "portal.php" is appended -- real Stalker clients do the
    same, since users typically copy a path like "/c/" or
    "/stalker_portal/c/" from their set-top box's settings screen, not the
    literal .php endpoint.

    Returns None if the scheme doesn't match, there's no host, or `mac` is
    missing or not a plausible MAC address -- a malformed stalker:// URL is
    a hard usage error, not something that should fall back to being
    treated as a direct stream.
    """
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("stalker", "stalkers"):
        return None
    if not parsed.hostname:
        return None

    query = urllib.parse.parse_qs(parsed.query)
    mac = (query.get("mac") or [None])[0]
    if not mac or not _MAC_RE.match(mac):
        return None

    scheme = "https" if parsed.scheme == "stalkers" else "http"
    port = f":{parsed.port}" if parsed.port else ""
    base_url = f"{scheme}://{parsed.hostname}{port}"

    path = parsed.path or "/"
    portal_path = path if path.endswith(".php") else path.rstrip("/") + "/portal.php"

    serial = (query.get("serial") or [None])[0]
    device_id = (query.get("device_id") or [None])[0]
    stb_type = (query.get("stb_type") or ["MAG250"])[0]

    return StalkerCreds(
        base_url=base_url, portal_path=portal_path, mac=mac, serial=serial, device_id=device_id, stb_type=stb_type
    )


def redact_stalker_url(source: str) -> str:
    """Mask all but the first two octets of the MAC in a stalker(s):// URL
    for logging/printing -- the MAC is the entire access credential for a
    Stalker subscription, same treatment tvdinner.xtream.redact_xtream_url
    gives a password. Returns non-stalker URLs (and stalker URLs with no
    mac param to mask) unchanged."""
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme not in ("stalker", "stalkers"):
        return source

    match = re.search(r"(?:^|[?&])mac=([^&]+)", parsed.query)
    if not match:
        return source

    mac = urllib.parse.unquote(match.group(1))
    parts = mac.split(":")
    redacted = ":".join(parts[:2] + ["**"] * (len(parts) - 2)) if len(parts) > 2 else "**"
    new_query = parsed.query[: match.start(1)] + redacted + parsed.query[match.end(1) :]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _local_timezone_name() -> str:
    try:
        return time.tzname[time.localtime().tm_isdst > 0] or "GMT"
    except Exception:
        return "GMT"


def _headers(creds: StalkerCreds, token: str | None) -> dict[str, str]:
    headers = {
        "User-Agent": _USER_AGENT,
        "X-User-Agent": f"Model: {creds.stb_type}; Link: WiFi",
        "Cookie": f"mac={creds.mac}; stb_lang=en; timezone={_local_timezone_name()}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class _StalkerApiError(Exception):
    pass


def _api_get(creds: StalkerCreds, params: dict[str, str], token: str | None, timeout: float) -> dict:
    url = f"{creds.base_url}{creds.portal_path}"
    try:
        response = requests.get(url, params=params, headers=_headers(creds, token), timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _StalkerApiError(f"Could not reach Stalker portal at {creds.base_url}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise _StalkerApiError(
            f"Stalker portal at {creds.base_url} did not return a valid response "
            "(check the portal URL and path)"
        ) from exc


def _fetch_items(creds: StalkerCreds, token: str, item_type: str, timeout: float) -> list[dict]:
    """Fetch every item of `item_type` ("itv" or "vod") from the portal.
    For "itv", tries the single-call get_all_channels action first (widely
    supported for live channels); falls back to (and "vod" always uses) the
    paginated get_ordered_list form, looping until total_items is reached."""
    if item_type == "itv":
        all_items_raw = _api_get(
            creds, {"type": item_type, "action": "get_all_channels", "JsHttpRequest": "1-xml"}, token, timeout
        )
        data = all_items_raw.get("js") if isinstance(all_items_raw, dict) else None
        items = data.get("data") if isinstance(data, dict) else None
        if items:
            return [item for item in items if isinstance(item, dict)]

    collected: list[dict] = []
    page = 1
    while True:
        page_raw = _api_get(
            creds,
            {"type": item_type, "action": "get_ordered_list", "genre": "*", "p": str(page), "JsHttpRequest": "1-xml"},
            token,
            timeout,
        )
        page_data = page_raw.get("js") if isinstance(page_raw, dict) else None
        page_items = page_data.get("data") if isinstance(page_data, dict) else None
        if not page_items:
            break
        collected.extend(item for item in page_items if isinstance(item, dict))
        total_items = page_data.get("total_items") if isinstance(page_data, dict) else None
        if total_items is None or len(collected) >= int(total_items):
            break
        page += 1
    return collected


def _fetch_categories(creds: StalkerCreds, token: str, item_type: str, timeout: float) -> dict[str, str]:
    """Fetch a {id: title} map of categories/genres for `item_type` ("itv"
    uses the get_genres action, "vod" uses get_categories) -- same response
    shape either way."""
    action = "get_genres" if item_type == "itv" else "get_categories"
    raw = _api_get(creds, {"type": item_type, "action": action, "JsHttpRequest": "1-xml"}, token, timeout)
    js = raw.get("js") if isinstance(raw, dict) else None
    return {
        str(category["id"]): category.get("title", "")
        for category in (js if isinstance(js, list) else [])
        if isinstance(category, dict) and category.get("id") is not None
    }


def _resolve_stream_url(
    creds: StalkerCreds, token: str, cmd: str, timeout: float, item_type: str = "itv"
) -> str | None:
    try:
        result = _api_get(
            creds,
            {"type": item_type, "action": "create_link", "cmd": cmd, "series": "", "JsHttpRequest": "1-xml"},
            token,
            timeout,
        )
    except _StalkerApiError:
        return None
    js = result.get("js") if isinstance(result, dict) else None
    link = js.get("cmd") if isinstance(js, dict) else None
    if not link:
        return None
    if link.startswith("ffmpeg "):
        link = link[len("ffmpeg ") :]
    return link.strip() or None


def load_stalker_playlist(creds: StalkerCreds, timeout: float = 15) -> tuple[Playlist | None, str | None]:
    """Log into a Stalker portal and build a Playlist from its channel
    list, with each channel's playable URL resolved once via create_link.
    Returns (playlist, None) on success, or (None, message) on a hard
    failure (unreachable portal, or no auth token returned) -- the caller
    should surface `message` and not attempt to play the stalker:// URL as
    a raw stream.
    """
    try:
        handshake = _api_get(
            creds, {"type": "stb", "action": "handshake", "token": "", "JsHttpRequest": "1-xml"}, None, timeout
        )
    except _StalkerApiError as exc:
        return None, str(exc)

    handshake_js = handshake.get("js") if isinstance(handshake, dict) else None
    token = handshake_js.get("token") if isinstance(handshake_js, dict) else None
    if not token:
        return None, "Could not authenticate with Stalker portal (no token returned) -- check the portal URL and path"

    try:
        _api_get(
            creds,
            {
                "type": "stb",
                "action": "get_profile",
                "mac": creds.mac,
                "sn": creds.serial or "",
                "stb_type": creds.stb_type,
                "device_id": creds.device_id or "",
                "JsHttpRequest": "1-xml",
            },
            token,
            timeout,
        )
    except _StalkerApiError as exc:
        # Not every portal fork requires this before channels resolve.
        logger.warning("Stalker get_profile call failed (continuing anyway): %s", exc)

    try:
        genres = _fetch_categories(creds, token, "itv", timeout)
        channels_raw = _fetch_items(creds, token, "itv", timeout)
    except _StalkerApiError as exc:
        return None, str(exc)

    def build_channel(raw: dict) -> Channel | None:
        cmd = raw.get("cmd")
        name = raw.get("name")
        if not cmd or not name:
            return None
        url = _resolve_stream_url(creds, token, cmd, timeout, item_type="itv")
        if url is None:
            logger.warning("Could not resolve stream URL for Stalker channel %r; skipping", name)
            return None

        logo = raw.get("logo") or None
        if logo and not logo.startswith(("http://", "https://")):
            logo = f"{creds.base_url.rstrip('/')}/{logo.lstrip('/')}"

        genre_id = raw.get("tv_genre_id")
        group_title = genres.get(str(genre_id)) if genre_id is not None else None

        xmltv_id = raw.get("xmltv_id") or None
        return Channel(
            name=str(name),
            url=url,
            tvg_id=str(xmltv_id) if xmltv_id else None,
            tvg_logo=logo,
            group_title=group_title,
        )

    with ThreadPoolExecutor(max_workers=_CREATE_LINK_WORKERS) as executor:
        channels = [channel for channel in executor.map(build_channel, channels_raw) if channel is not None]

    return Playlist(channels=channels), None


def load_stalker_vod(creds: StalkerCreds, timeout: float = 15) -> tuple[list[VodItem], str | None]:
    """Log into a Stalker portal and build a list of VodItems from its VOD
    (movies) library, with each item's playable URL resolved via
    create_link exactly like load_stalker_playlist does for live channels
    -- same bounded ThreadPoolExecutor, same tradeoff (fine for
    hundreds-to-low-thousands of items; a very large VOD library would slow
    startup, since unlike Xtream a Stalker item has no static playable URL).
    Returns (items, None) on success, or ([], message) on a hard failure --
    meant to be treated as non-fatal by the caller, since VOD is
    supplementary to live TV."""
    try:
        handshake = _api_get(
            creds, {"type": "stb", "action": "handshake", "token": "", "JsHttpRequest": "1-xml"}, None, timeout
        )
    except _StalkerApiError as exc:
        return [], str(exc)

    handshake_js = handshake.get("js") if isinstance(handshake, dict) else None
    token = handshake_js.get("token") if isinstance(handshake_js, dict) else None
    if not token:
        return [], "Could not authenticate with Stalker portal (no token returned) -- check the portal URL and path"

    try:
        categories = _fetch_categories(creds, token, "vod", timeout)
        items_raw = _fetch_items(creds, token, "vod", timeout)
    except _StalkerApiError as exc:
        return [], str(exc)

    def build_item(raw: dict) -> VodItem | None:
        cmd = raw.get("cmd")
        name = raw.get("name")
        if not cmd or not name:
            return None
        url = _resolve_stream_url(creds, token, cmd, timeout, item_type="vod")
        if url is None:
            logger.warning("Could not resolve stream URL for Stalker VOD item %r; skipping", name)
            return None

        poster = raw.get("screenshot_uri") or raw.get("cover_big") or None
        if poster and not poster.startswith(("http://", "https://")):
            poster = f"{creds.base_url.rstrip('/')}/{poster.lstrip('/')}"

        category_id = raw.get("category_id")
        group_title = categories.get(str(category_id)) if category_id is not None else None

        year = raw.get("year") or None
        return VodItem(
            title=str(name),
            url=url,
            group_title=group_title,
            poster_url=poster,
            year=str(year) if year else None,
        )

    with ThreadPoolExecutor(max_workers=_CREATE_LINK_WORKERS) as executor:
        items = [item for item in executor.map(build_item, items_raw) if item is not None]

    return items, None
