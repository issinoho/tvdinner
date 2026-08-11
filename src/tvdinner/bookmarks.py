"""Saved playlist bookmarks: a description plus a URL (M3U, Xtream Codes,
Stalker Portal, HDHomeRun, a direct stream, or a local video file --
anything the `url` positional argument accepts), optional XMLTV EPG URL
and default channel, and an optional per-bookmark TMDB API token (like
--tmdb-api-token -- for a local video file bookmark, this is what enables
its 'i' overlay's poster/synopsis/rating, same as typing
--tmdb-api-token directly), so a frequently-used source doesn't need to
be retyped every time -- see tvdinner.bookmarks_tui for the interactive
picker.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_BOOKMARKS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "bookmarks.json"
else:
    DEFAULT_BOOKMARKS_PATH = Path.home() / ".config" / "tvdinner" / "bookmarks.json"


@dataclass
class Bookmark:
    name: str
    url: str
    epg: str | None = None
    channel: str | None = None  # channel name (or 1-based index), like -c/--channel
    tmdb_api_token: str | None = None  # like --tmdb-api-token; see tvdinner.bookmarks_tui for why the table never shows it


def load_bookmarks(path: Path) -> tuple[list[Bookmark], list[str]]:
    """Load saved bookmarks from a JSON file, e.g.:

        [
          {"name": "My Provider", "url": "https://example.com/playlist.m3u",
           "epg": "https://example.com/guide.xml", "channel": "CNN"},
          {"name": "Local test", "url": "test.m3u", "epg": null, "channel": null}
        ]

    A missing file is not an error -- it just means no bookmarks yet.
    Malformed JSON, or a malformed individual entry, is reported as a
    warning string rather than raising, so one bad entry doesn't prevent
    the rest from loading; the caller decides how to surface them (e.g.
    printed to stderr)."""
    if not path.is_file():
        return [], []

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"Could not read bookmarks file {path}: {exc}"]

    if not isinstance(data, list):
        return [], [f"Bookmarks file {path} must contain a JSON array of entries"]

    bookmarks: list[Bookmark] = []
    warnings: list[str] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not isinstance(
            entry.get("url"), str
        ):
            warnings.append(f"Ignoring malformed bookmark entry {index} in {path}")
            continue
        epg = entry.get("epg")
        if epg is not None and not isinstance(epg, str):
            epg = None
        channel = entry.get("channel")
        if channel is not None and not isinstance(channel, str):
            channel = None
        tmdb_api_token = entry.get("tmdb_api_token")
        if tmdb_api_token is not None and not isinstance(tmdb_api_token, str):
            tmdb_api_token = None
        bookmarks.append(
            Bookmark(name=entry["name"], url=entry["url"], epg=epg, channel=channel, tmdb_api_token=tmdb_api_token)
        )
    return bookmarks, warnings


def save_bookmarks(path: Path, bookmarks: list[Bookmark]) -> None:
    """Write bookmarks back to their JSON file, preserving list order.
    Creates the parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {"name": b.name, "url": b.url, "epg": b.epg, "channel": b.channel, "tmdb_api_token": b.tmdb_api_token}
        for b in bookmarks
    ]
    path.write_text(json.dumps(data, indent=2) + "\n")
