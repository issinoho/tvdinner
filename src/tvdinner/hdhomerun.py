"""HDHomeRun (SiliconDust) network tuner support.

An HDHomeRun device streams live TV (OTA/cable) over the LAN via a simple,
unauthenticated HTTP JSON API -- no username/password/MAC to present, and
unlike a Stalker Portal channel's "cmd" field (see tvdinner.stalker), each
lineup entry's URL is already a directly playable stream URL with no
per-channel resolve step needed. This module fetches a device's
discover.json (to find its lineup URL and confirm it's actually an
HDHomeRun device) and then its lineup.json, mapping the result onto the
same Playlist/Channel objects the M3U loader produces, so the rest of the
app needs no changes to support it.

A device's IP is not a secret, so unlike tvdinner.xtream/tvdinner.stalker
there is no redact_*_url helper here -- nothing about an hdhomerun:// URL
needs masking in logs.

EPG data, when available, comes from SiliconDust's own cloud XMLTV export
(see _EPG_URL_TEMPLATE below) -- real XMLTV, so tvdinner.epg needs no
changes to consume it, exactly like an Xtream Codes login's xmltv.php.
That API requires a paid HDHomeRun DVR guide subscription; a device
without one will simply fail to fetch it, which tvdinner.epg's existing
network-failure handling already turns into a logged warning and "EPG
data not available" -- the same graceful degradation any other
inaccessible EPG source already gets, so no special-casing is needed here
beyond setting epg_url when a DeviceAuth is available to try.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass

import requests

from tvdinner.m3u import Channel, Playlist

logger = logging.getLogger(__name__)

# https://info.hdhomerun.com/info/dvr:xmltv -- 14-day XMLTV guide data,
# gated behind a paid HDHomeRun DVR guide subscription. DeviceAuth comes
# from discover.json and rotates roughly every 8-24 hours; fetched fresh
# here on every load_hdhomerun_playlist() call, which is what tvdinner
# does on every invocation anyway. SiliconDust asks that this not be
# polled on a fixed schedule (e.g. every day at midnight) -- fine for a
# foreground, interactively-launched CLI like tvdinner, whose refresh
# moments are already scattered across the day by when users start it,
# but worth remembering if this project ever grows a daemon/background-
# refresh mode.
_EPG_URL_TEMPLATE = "https://api.hdhomerun.com/api/xmltv?DeviceAuth={device_auth}"


@dataclass
class HdHomeRunTarget:
    base_url: str  # e.g. "http://192.168.1.50:80", no trailing slash


def is_hdhomerun_url(source: str) -> bool:
    return urllib.parse.urlsplit(source).scheme == "hdhomerun"


def parse_hdhomerun_url(source: str) -> HdHomeRunTarget | None:
    """Parse an `hdhomerun://host[:port]` URL (default port 80 -- real
    devices only ever serve plain HTTP on the LAN, so there's no https
    variant). Returns None if the scheme doesn't match or there's no host
    -- a malformed hdhomerun:// URL is a hard usage error, not something
    that should fall back to being treated as a direct stream."""
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme != "hdhomerun":
        return None
    if not parsed.hostname:
        return None

    port = f":{parsed.port}" if parsed.port else ""
    return HdHomeRunTarget(base_url=f"http://{parsed.hostname}{port}")


class _HdHomeRunError(Exception):
    pass


def _get_json(url: str, timeout: float, *, not_found_message: str) -> dict | list:
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise _HdHomeRunError(f"Could not reach HDHomeRun device at {url}: {exc}") from exc
    try:
        return response.json()
    except ValueError as exc:
        raise _HdHomeRunError(not_found_message) from exc


def load_hdhomerun_playlist(target: HdHomeRunTarget, timeout: float = 15) -> tuple[Playlist | None, str | None]:
    """Fetch an HDHomeRun device's discover.json (to find its lineup URL
    and confirm it's actually an HDHomeRun device) and lineup.json,
    building a Playlist from the result. Returns (playlist, None) on
    success, or (None, message) on a hard failure (unreachable device, or
    a response that doesn't look like an HDHomeRun device) -- the caller
    should surface `message` and not attempt to play the hdhomerun:// URL
    as a raw stream.
    """
    not_hdhomerun_message = f"{target.base_url} does not look like an HDHomeRun device"
    try:
        discover = _get_json(f"{target.base_url}/discover.json", timeout, not_found_message=not_hdhomerun_message)
    except _HdHomeRunError as exc:
        return None, str(exc)

    lineup_url = discover.get("LineupURL") if isinstance(discover, dict) else None
    if not lineup_url:
        return None, not_hdhomerun_message

    logger.info(
        "Connected to HDHomeRun device %r (DeviceID=%s)",
        discover.get("FriendlyName", target.base_url),
        discover.get("DeviceID"),
    )

    device_auth = discover.get("DeviceAuth")
    epg_url = _EPG_URL_TEMPLATE.format(device_auth=device_auth) if device_auth else None

    try:
        lineup = _get_json(lineup_url, timeout, not_found_message=not_hdhomerun_message)
    except _HdHomeRunError as exc:
        return None, str(exc)

    channels: list[Channel] = []
    for entry in lineup if isinstance(lineup, list) else []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("URL")
        name = entry.get("GuideName")
        if not url or not name:
            continue
        guide_number = entry.get("GuideNumber")
        channels.append(Channel(name=str(name), url=str(url), tvg_id=str(guide_number) if guide_number else None))

    return Playlist(channels=channels, epg_url=epg_url), None
