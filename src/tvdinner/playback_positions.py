"""Per-recording playback position persistence, so resuming a recording
(see the 'w' recordings browser) picks up where you left off instead of
starting over -- every time playback stops or switches away from a
recording, and again on shutdown, cli.py saves the current position here.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from datetime import timedelta
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_PLAYBACK_POSITIONS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "playback_positions.json"
else:
    DEFAULT_PLAYBACK_POSITIONS_PATH = Path.home() / ".config" / "tvdinner" / "playback_positions.json"

# How long a VOD (remote-URL) resume position is kept once nothing resumes
# or updates it -- generous enough to comfortably pick a paused movie back
# up after a long break, but bounded so browsing a lot of VOD content over
# months doesn't leave this file growing forever with entries for items
# that were watched once, abandoned, and never opened again. A local
# recording's entry has no such limit -- see save_playback_positions --
# since the file itself sticking around already implies it's still worth
# resuming.
DEFAULT_PLAYBACK_POSITION_MAX_AGE = timedelta(days=90)


def playback_position_timestamps_path_for(path: Path) -> Path:
    """The sibling file recording, for each remote (VOD) key in `path`,
    when its position was last actually saved -- see save_playback_positions.
    Public so cli.py's hard-reset can delete it alongside the positions
    file itself, same as epg.py's parsed_cache_path_for is public for its
    own sibling-cache-file reasoning."""
    return path.with_name(f"{path.stem}_timestamps.json")


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


def _load_timestamps(path: Path) -> dict[str, float]:
    """{key: epoch seconds last touched}, for whichever remote keys the
    sibling timestamps file (see playback_position_timestamps_path_for)
    has recorded -- missing/corrupt/malformed is treated as "nothing
    recorded", same forgiving contract as load_playback_positions's own
    malformed-entry handling, just without warnings since this is an
    internal-only file no caller ever inspects directly."""
    ts_path = playback_position_timestamps_path_for(path)
    if not ts_path.is_file():
        return {}
    try:
        data = json.loads(ts_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: float(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _is_remote(key: str) -> bool:
    return urllib.parse.urlsplit(key).scheme in ("http", "https")


def save_playback_positions(
    path: Path,
    positions: dict[str, float],
    *,
    touched_key: str | None = None,
    max_age: timedelta = DEFAULT_PLAYBACK_POSITION_MAX_AGE,
) -> None:
    """Persist {recording path or VOD url: position in seconds}, dropping
    any local-file entry whose file no longer exists (otherwise this would
    grow forever as old recordings get deleted -- see the 'd'
    recordings-browser key), and any remote (VOD) entry untouched for
    longer than `max_age` -- there's no cheap way to check a remote item
    still exists, so unlike a local file it can't be pruned by existence,
    but keeping it *unconditionally* forever (the previous behavior) meant
    a VOD item started once and never resumed just sat here forever too.
    Creates the parent directory if needed.

    `touched_key`, when given, is the one key actually just played --
    stamped as touched right now in the sibling timestamps file (see
    playback_position_timestamps_path_for). Every other remote key's
    last-touched time is carried over unchanged from that file, or, for a
    key seen there for the first time (a fresh entry from this same save,
    or one saved by a version of tvdinner that predates this file),
    stamped as touched now rather than treated as already-expired -- an
    upgrade must never mass-prune existing resume positions just because
    their real history isn't known yet."""
    now = time.time()
    timestamps = _load_timestamps(path)
    if touched_key is not None and touched_key in positions:
        timestamps[touched_key] = now

    pruned: dict[str, float] = {}
    kept_timestamps: dict[str, float] = {}
    for key, seconds in positions.items():
        if _is_remote(key):
            last_touched = timestamps.get(key, now)
            if (now - last_touched) >= max_age.total_seconds():
                continue
            kept_timestamps[key] = last_touched
        elif not Path(key).exists():
            continue
        pruned[key] = seconds

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pruned, indent=2, sort_keys=True) + "\n")
    try:
        playback_position_timestamps_path_for(path).write_text(json.dumps(kept_timestamps, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass  # best-effort, same tolerance as the rest of this app's disk-cache writes
    # A remote key here is a VOD/Plex stream URL, which can carry an
    # Xtream login's own username/password or a Plex token -- best-
    # effort, matches gdrive.py's own credentials file. Covers the
    # sibling timestamps file too, since its keys are the same URLs.
    for hardened in (path, playback_position_timestamps_path_for(path)):
        try:
            hardened.chmod(0o600)
        except OSError:
            pass  # not every filesystem supports it
