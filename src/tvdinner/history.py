"""Persisted watch history: every live channel, VOD item, or recording
actually watched, with when and for how long -- browsable in-app via
cli.py's 'x' keybinding (see overlay.render_history_browser), and
captured with enough cover-art/rating/director detail (best-effort,
whatever the source actually supplied) to make that browser feel like a
real "recently watched" view rather than a bare log.

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
    # For "vod"/"recording", the movie/recording's own title, same as
    # always. For "channel", the *programme* that was actually airing
    # when the watch started (e.g. "EastEnders"), not the channel's own
    # name -- see channel_name below for that -- falling back to the
    # channel's name only when no EPG data identifies what was on.
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
    # "channel" only: which channel it aired on (title above is the
    # programme, not this) -- None for "vod"/"recording", where there's
    # no separate channel concept.
    channel_name: str | None = None
    # Everything below is best-effort cover art/metadata for the history
    # browser (cli.py's 'x' keybinding) -- always None for a "recording"
    # entry (no provider to source it from), and for "channel"/"vod" only
    # when whatever the source could supply was actually available at the
    # moment the entry was recorded (e.g. no --tmdb-api-token, an EPG
    # feed that doesn't tag a programme's poster/year/director, or a
    # movie TMDB had no match for). Captured at entry-close time, not
    # open time, so a VOD item's async TMDB/oEmbed lookup (see cli.py's
    # vod_metadata_loader) has had time to land first -- a "channel"
    # entry's own EPG lookup doesn't need that same deferral (the EPG
    # feed is already loaded well before any mid-session channel switch)
    # but is captured in the same place for one consistent code path.
    image_url: str | None = None  # VOD poster, a programme's own EPG poster, or a channel's tvg_logo
    year: str | None = None  # VOD, or a programme's own EPG <date>
    rating: str | None = None  # VOD only -- XMLTV has no per-programme rating field
    rating_is_tmdb: bool = False  # VOD only -- see vod.VodItem's own field of the same name
    director: str | None = None  # VOD, or a programme's own EPG <credits><director>

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
        "channel_name": entry.channel_name,
        "image_url": entry.image_url,
        "year": entry.year,
        "rating": entry.rating,
        "rating_is_tmdb": entry.rating_is_tmdb,
        "director": entry.director,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(payload) + "\n")
    try:
        # `url`/`image_url` can carry an Xtream login's own username/
        # password or a Plex token -- best-effort, matches gdrive.py's
        # own credentials file.
        path.chmod(0o600)
    except OSError:
        pass  # not every filesystem supports it


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
                    channel_name=data.get("channel_name"),
                    image_url=data.get("image_url"),
                    year=data.get("year"),
                    rating=data.get("rating"),
                    rating_is_tmdb=bool(data.get("rating_is_tmdb", False)),
                    director=data.get("director"),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            warnings.append(f"Ignoring malformed history entry at {path}:{line_number}: {exc}")
    return entries, warnings
