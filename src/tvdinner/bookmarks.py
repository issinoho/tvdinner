"""Saved playlist bookmarks: a description plus a URL (M3U, Xtream Codes,
Stalker Portal, HDHomeRun, a direct stream, or a local video file --
anything the `url` positional argument accepts), optional XMLTV EPG URL
and default channel, and an optional per-bookmark TMDB API token (like
--tmdb-api-token -- for a local video file bookmark, this is what enables
its 'i' overlay's poster/synopsis/rating, same as typing
--tmdb-api-token directly), so a frequently-used source doesn't need to
be retyped every time -- see tvdinner.bookmarks_tui for the interactive
picker, and run_bookmarks_{list,add,edit,remove}_command in tvdinner.cli
for the non-interactive `tvdinner bookmarks list|add|edit|remove` verbs
(scripting, and other tools registering a source -- e.g. a tvtimes
export -- as a bookmark).
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


def bookmark_to_dict(bookmark: Bookmark) -> dict[str, str | None]:
    """The on-disk / JSON representation of one bookmark -- the shape
    load_bookmarks reads back and `tvdinner bookmarks list --json` emits."""
    return {
        "name": bookmark.name,
        "url": bookmark.url,
        "epg": bookmark.epg,
        "channel": bookmark.channel,
        "tmdb_api_token": bookmark.tmdb_api_token,
    }


def save_bookmarks(path: Path, bookmarks: list[Bookmark]) -> None:
    """Write bookmarks back to their JSON file, preserving list order.
    Creates the parent directory if needed. The write is atomic -- a temp
    file in the same directory, then os.replace -- so a concurrent reader
    (the interactive picker, another `tvdinner bookmarks` process) never
    sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps([bookmark_to_dict(b) for b in bookmarks], indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        try:
            # A bookmark's own url can carry an Xtream/Stalker login's
            # username/password or a Plex token -- best-effort, matches
            # gdrive.py's own credentials file.
            tmp.chmod(0o600)
        except OSError:
            pass  # not every filesystem supports it
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class BookmarkError(ValueError):
    """A bookmark add/edit/remove couldn't be completed as asked -- a name
    clash on add, or nothing matching the given name/index on remove."""


def find_bookmark(bookmarks: list[Bookmark], key: str) -> tuple[int, Bookmark] | None:
    """Resolve a bookmark by an all-digits `key` (its 1-based position) or
    else an exact name match -- the same "name or 1-based index" rule
    -c/--channel uses. Returns (index, bookmark), or None if a numeric key
    is out of range or a name doesn't match. A numeric key is never tried
    as a name."""
    if key.isdigit():
        position = int(key) - 1
        if 0 <= position < len(bookmarks):
            return position, bookmarks[position]
        return None
    for index, bookmark in enumerate(bookmarks):
        if bookmark.name == key:
            return index, bookmark
    return None


def upsert_bookmark(
    bookmarks: list[Bookmark], bookmark: Bookmark, *, replace: bool = False
) -> tuple[list[Bookmark], bool]:
    """Return a new list with `bookmark` appended, or -- when `replace` and
    a row with the same name already exists -- swapped in at that row's
    position. Returns (new_list, replaced_in_place). Raises BookmarkError
    on a name clash when `replace` is false. List order is preserved."""
    updated = list(bookmarks)
    for index, existing in enumerate(updated):
        if existing.name == bookmark.name:
            if not replace:
                raise BookmarkError(
                    f"A bookmark named {bookmark.name!r} already exists (use `edit`, or `add --replace`)"
                )
            updated[index] = bookmark
            return updated, True
    updated.append(bookmark)
    return updated, False


def remove_bookmark(bookmarks: list[Bookmark], key: str) -> tuple[list[Bookmark], Bookmark]:
    """Return a new list with the bookmark matched by `key` (name or
    1-based index, see find_bookmark) dropped, plus the row removed. Raises
    BookmarkError if nothing matches."""
    found = find_bookmark(bookmarks, key)
    if found is None:
        raise BookmarkError(f"No bookmark matches {key!r}")
    index, removed = found
    return bookmarks[:index] + bookmarks[index + 1 :], removed
