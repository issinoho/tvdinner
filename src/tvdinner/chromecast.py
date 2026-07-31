"""Google Cast (Chromecast) support.

A purely optional dependency (see pyproject.toml's "chromecast" extra,
`pip install tvdinner[chromecast]`) -- this module degrades to
chromecast_available() returning False rather than raising ImportError at
load time, so the rest of the app works identically whether or not the
extra is installed. Casting to a device just tells it to independently
fetch and play a URL (the same URL tvdinner itself is already playing)
via Chromecast's own Default Media Receiver app -- tvdinner never proxies
or transcodes the stream bytes itself. Confirmed live: raw MPEG-TS (most
live IPTV channels) has only limited native decode support on real
Chromecast hardware, unlike tvdinner's own mpv/ffmpeg playback -- a
receiver that can't decode a given stream will simply fail to cast it;
there's no transcoding fallback here.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

try:
    import pychromecast
except ImportError:
    pychromecast = None

logger = logging.getLogger(__name__)


def chromecast_available() -> bool:
    return pychromecast is not None


@dataclass
class CastDevice:
    """One discovered Chromecast, for cli.py's device-picker list.
    `cast` is the live pychromecast.Chromecast object underneath --
    opaque to overlay.py (which only ever reads `.name`), passed straight
    back into cast_url/stop_casting once the user picks a row."""

    name: str
    cast: "pychromecast.Chromecast"


_CONTENT_TYPES = {
    ".m3u8": "application/x-mpegurl",
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


def guess_content_type(url: str) -> str:
    """Best-effort MIME type for a stream URL, from its path extension --
    Chromecast's receiver uses this to decide how to request/decode the
    stream. Falls back to raw MPEG-TS (the common shape for a live IPTV
    channel URL with no file extension at all)."""
    path = urllib.parse.urlsplit(url).path.lower()
    for suffix, content_type in _CONTENT_TYPES.items():
        if path.endswith(suffix):
            return content_type
    return "video/mp2t"


def discover_chromecasts(on_device_found: Callable[["pychromecast.Chromecast"], None]) -> Callable[[], None]:
    """Start background discovery, calling `on_device_found` with a live,
    ready-to-cast Chromecast object for each device found -- mDNS
    discovery takes real seconds, so this never blocks the caller.
    Returns a function that stops discovery; call it once the caller is
    done (e.g. the device-picker overlay is closed) to release the
    underlying zeroconf listener rather than leaking it."""
    browser = pychromecast.get_chromecasts(blocking=False, callback=on_device_found)
    return browser.stop_discovery


def cast_url(cast: "pychromecast.Chromecast", url: str, title: str, live: bool) -> None:
    """Tell `cast` to fetch and play `url` independently -- the exact URL
    tvdinner itself is already playing, not a proxied or transcoded copy.
    `live` selects Chromecast's own LIVE vs BUFFERED stream-type hint (a
    live channel has no seek bar/duration on the receiver; VOD/Plex items
    do)."""
    cast.wait()
    cast.media_controller.play_media(
        url, guess_content_type(url), title=title, stream_type="LIVE" if live else "BUFFERED"
    )


def stop_casting(cast: "pychromecast.Chromecast") -> None:
    """Stop whatever `cast` is currently playing and quit its receiver
    app, handing the TV/speaker back to its own home screen."""
    cast.media_controller.stop()
    cast.quit_app()
