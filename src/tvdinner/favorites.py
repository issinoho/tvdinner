"""Per-feed favorite channel persistence.

Favorites are scoped to the playlist ("feed") they came from -- keyed by
its exact source string (the URL or file path given on the command line)
in a single shared file, so two different providers' channels that
happen to share a display name don't collide, and each playlist's
favorites travel with it across runs without one giant list mixing
everything together.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_FAVORITES_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "favorites.json"
else:
    DEFAULT_FAVORITES_PATH = Path.home() / ".config" / "tvdinner" / "favorites.json"


def load_favorites(path: Path, feed: str) -> tuple[set[str], list[str]]:
    """Load the favorited channel names (keyed by display name, like
    --epg-shifts -- see load_channel_shifts) for one feed from the shared
    favorites file, e.g.:

        {"https://example.com/playlist.m3u": ["BBC One", "Channel 4"]}

    A missing file, or a feed with no favorites yet, is not an error --
    it just means no favorites. Malformed JSON or a malformed entry is
    reported as a warning string rather than raising, so one bad file
    doesn't prevent the whole app from starting; the caller decides how
    to surface them (e.g. printed to stderr)."""
    if not path.is_file():
        return set(), []

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return set(), [f"Could not read favorites file {path}: {exc}"]

    if not isinstance(data, dict):
        return set(), [f"Favorites file {path} must contain a JSON object mapping feed to a list of names"]

    names = data.get(feed, [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return set(), [f"Favorites entry for {feed!r} in {path} is not a list of channel names; ignoring"]
    return set(names), []


def save_favorites(path: Path, feed: str, favorites: set[str]) -> None:
    """Persist the favorited channel names for one feed, merging with
    whatever other feeds' entries are already in the shared file (read
    fresh here rather than trusting a caller's possibly-stale in-memory
    copy of them). Creates the parent directory if needed."""
    data: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict):
                data = existing
        except (OSError, json.JSONDecodeError):
            pass

    data[feed] = sorted(favorites)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
