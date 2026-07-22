"""Parser for IPTV M3U/M3U8 playlists (the #EXTM3U/#EXTINF channel-list format,
not the HLS media-segment variant)."""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_DURATION_RE = re.compile(r"^-?\d+(?:\.\d+)?\s*(.*)$")


@dataclass
class Channel:
    name: str
    url: str
    tvg_id: str | None = None
    tvg_name: str | None = None
    tvg_logo: str | None = None
    group_title: str | None = None
    tvg_url: str | None = None  # per-channel EPG override, rarely used


@dataclass
class Playlist:
    channels: list[Channel] = field(default_factory=list)
    epg_url: str | None = None  # from x-tvg-url/url-tvg on the #EXTM3U header line


def _parse_extinf(line: str) -> tuple[dict[str, str], str]:
    """Parse a #EXTINF line's body (after the '#EXTINF:' prefix) into
    (attributes, display_name)."""
    match = _DURATION_RE.match(line)
    rest = match.group(1) if match else line

    attrs = dict(_ATTR_RE.findall(rest))
    stripped = _ATTR_RE.sub("", rest)
    name = stripped.split(",", 1)[1].strip() if "," in stripped else stripped.strip()
    return attrs, name


def parse_m3u(text: str) -> Playlist:
    """Parse M3U playlist text into a Playlist of channels."""
    playlist = Playlist()
    pending_attrs: dict[str, str] | None = None
    pending_name: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTM3U"):
            header_attrs = dict(_ATTR_RE.findall(line))
            playlist.epg_url = header_attrs.get("x-tvg-url") or header_attrs.get("url-tvg")
            continue

        if line.startswith("#EXTINF:"):
            pending_attrs, pending_name = _parse_extinf(line[len("#EXTINF:"):])
            continue

        if line.startswith("#"):
            continue  # ignore other directives (#EXTGRP, #EXTVLCOPT, etc.)

        # A non-comment line following an #EXTINF is the stream URL.
        if pending_name is not None:
            attrs = pending_attrs or {}
            playlist.channels.append(
                Channel(
                    name=pending_name,
                    url=line,
                    tvg_id=attrs.get("tvg-id") or None,
                    tvg_name=attrs.get("tvg-name") or None,
                    tvg_logo=attrs.get("tvg-logo") or None,
                    group_title=attrs.get("group-title") or None,
                    tvg_url=attrs.get("tvg-url") or None,
                )
            )
            pending_attrs, pending_name = None, None
        # A bare URL with no preceding #EXTINF is malformed for this format; skip it.

    return playlist


def _looks_like_m3u(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith("#EXTM3U")
    return False


def _fetch_text(source: str) -> str | None:
    parsed = urllib.parse.urlparse(source)

    if parsed.scheme in ("http", "https"):
        try:
            response = requests.get(source, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Could not fetch playlist %s: %s", source, exc)
            return None
        return response.text

    if parsed.scheme in ("", "file"):
        path = Path(parsed.path if parsed.scheme == "file" else source)
        if path.is_file():
            try:
                return path.read_text(errors="replace")
            except OSError as exc:
                logger.warning("Could not read playlist %s: %s", path, exc)
                return None
        return None

    # Other schemes (udp, rtp, rtmp, etc.) are stream URLs, not playlist sources.
    return None


def load_playlist(source: str) -> Playlist | None:
    """Fetch and parse an M3U playlist from an http(s) URL or local file path.

    Returns None if the source can't be read as text or doesn't look like an
    M3U playlist, so callers can fall back to treating it as a direct stream
    URL.
    """
    text = _fetch_text(source)
    if text is None or not _looks_like_m3u(text):
        return None
    return parse_m3u(text)
