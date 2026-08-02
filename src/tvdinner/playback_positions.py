"""Per-recording playback position persistence, so resuming a recording
(see the 'w' recordings browser) picks up where you left off instead of
starting over -- every time playback stops or switches away from a
recording, and again on shutdown, cli.py saves the current position here.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_PLAYBACK_POSITIONS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "playback_positions.json"
else:
    DEFAULT_PLAYBACK_POSITIONS_PATH = Path.home() / ".config" / "tvdinner" / "playback_positions.json"


def load_playback_positions(path: Path) -> tuple[dict[str, float], list[str]]:
    """Load saved {recording path: position in seconds} from a JSON file,
    e.g.:

        {"/home/user/Videos/tvdinner/BBC One_20260726-200000.ts": 843.2}

    A missing file is not an error -- it just means nothing saved yet.
    Malformed JSON, or a malformed individual entry, is reported as a
    warning string rather than raising, so one bad entry doesn't prevent
    the rest from loading; the caller decides how to surface them (e.g.
    printed to stderr)."""
    if not path.is_file():
        return {}, []

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"Could not read playback positions file {path}: {exc}"]

    if not isinstance(data, dict):
        return {}, [f"Playback positions file {path} must contain a JSON object mapping path to seconds"]

    positions: dict[str, float] = {}
    warnings: list[str] = []
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, (int, float)) or isinstance(value, bool):
            warnings.append(f"Ignoring malformed playback position entry {key!r} in {path}")
            continue
        positions[key] = float(value)
    return positions, warnings


def _still_valid(key: str) -> bool:
    """A key is either a local recording file path (prune it once the file
    is gone) or a remote VOD stream URL (there's no cheap way to check a
    remote item still exists, and Path(url).exists() is always False for
    one -- so keep it unconditionally rather than silently losing VOD
    resume positions on every save)."""
    if urllib.parse.urlsplit(key).scheme in ("http", "https"):
        return True
    return Path(key).exists()


def save_playback_positions(path: Path, positions: dict[str, float]) -> None:
    """Persist {recording path or VOD url: position in seconds}, dropping
    any local-file entry whose file no longer exists -- otherwise this
    would grow forever as old recordings get deleted (see the 'd'
    recordings-browser key). Creates the parent directory if needed."""
    pruned = {p: seconds for p, seconds in positions.items() if _still_valid(p)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pruned, indent=2, sort_keys=True) + "\n")
