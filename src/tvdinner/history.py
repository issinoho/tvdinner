"""Persisted watch history: every live channel, VOD item, or recording
actually watched, with when and for how long -- captured for possible
future use (e.g. a "recently watched" view, or usage stats) even though
nothing reads it back yet.

Append-only, unlike this codebase's other load_*/save_* config pairs
(favorites.py, bookmarks.py, schedule.py, playback_positions.py): those
each represent current state (a handful of entries, rewritten whole on
every change), where history is meant to grow indefinitely. Entries are
appended one JSON object per line (JSONL) instead, so writing a new one
is O(1) regardless of how large the log has already grown -- a
read-whole-file/write-whole-file pattern would get slower every session
as history piled up.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

if sys.platform == "win32":
    DEFAULT_HISTORY_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "history.jsonl"
else:
    DEFAULT_HISTORY_PATH = Path.home() / ".config" / "tvdinner" / "history.jsonl"

HistoryKind = Literal["channel", "vod", "recording"]

# Below this, a watch isn't recorded at all -- filters out the sub-few-
# second blips from flipping past channels while browsing the guide,
# rather than cluttering the log with entries nobody actually watched.
MIN_HISTORY_DURATION_SECONDS = 5.0


@dataclass
class HistoryEntry:
    kind: HistoryKind
    title: str
    url: str
    # The playlist/login/server this came from (redacted, e.g. an Xtream
    # login's password or a Plex token) -- None for a source with no
    # playlist concept (a local file, a YouTube video, a bare direct-
    # stream URL). Constant for a whole tvdinner session: browsing
    # channels/VOD/recordings never crosses from one playlist to another
    # mid-session, so this only needs setting once, at launch.
    playlist_source: str | None
    started_at: datetime  # tz-aware, UTC
    ended_at: datetime  # tz-aware, UTC

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


def append_history_entry(path: Path, entry: HistoryEntry) -> None:
    """Append `entry` as a single JSON line, unless it's shorter than
    MIN_HISTORY_DURATION_SECONDS (see the module docstring -- silently
    not recorded, not an error). Creates the parent directory if needed.
    Raises OSError on failure, like every other save_*/append_* in this
    codebase -- the caller decides how to handle it (cli.py treats a
    write failure here as best-effort and never lets it interrupt
    playback, the same tolerance it gives a playback-position save)."""
    if entry.duration_seconds < MIN_HISTORY_DURATION_SECONDS:
        return
    payload = {
        "kind": entry.kind,
        "title": entry.title,
        "url": entry.url,
        "playlist_source": entry.playlist_source,
        "started_at": entry.started_at.isoformat(),
        "ended_at": entry.ended_at.isoformat(),
        "duration_seconds": round(entry.duration_seconds, 1),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(payload) + "\n")


def load_history(path: Path) -> tuple[list[HistoryEntry], list[str]]:
    """Load every entry from the history log, oldest first. A missing
    file is not an error -- it just means nothing's been watched yet (or
    history was disabled via --no-history). A malformed individual line
    is reported as a warning string and skipped rather than raising,
    matching every other load_* in this codebase, so one bad line can't
    lose the rest of the log."""
    if not path.is_file():
        return [], []
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        return [], [f"Could not read history file {path}: {exc}"]

    entries: list[HistoryEntry] = []
    warnings: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            entries.append(
                HistoryEntry(
                    kind=data["kind"],
                    title=data["title"],
                    url=data["url"],
                    playlist_source=data.get("playlist_source"),
                    started_at=datetime.fromisoformat(data["started_at"]),
                    ended_at=datetime.fromisoformat(data["ended_at"]),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            warnings.append(f"Ignoring malformed history entry at {path}:{line_number}: {exc}")
    return entries, warnings
