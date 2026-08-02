"""Persisted EPG-scheduled recordings: schedule a specific guide
programme (current or upcoming) from its details popup, and tvdinner
switches to its channel and records it automatically when the time
comes -- as long as tvdinner is still running, since there's no
background service. See tvdinner.cli's schedule poll loop for the
part that actually acts on these.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_SCHEDULE_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "schedule.json"
else:
    DEFAULT_SCHEDULE_PATH = Path.home() / ".config" / "tvdinner" / "schedule.json"


@dataclass
class ScheduledRecording:
    id: str
    channel_url: str
    channel_name: str
    title: str
    start: datetime  # tz-aware, UTC
    stop: datetime  # tz-aware, UTC

    @staticmethod
    def create(channel_url: str, channel_name: str, title: str, start: datetime, stop: datetime) -> "ScheduledRecording":
        return ScheduledRecording(
            id=str(uuid.uuid4()), channel_url=channel_url, channel_name=channel_name, title=title, start=start, stop=stop
        )


def load_schedule(path: Path) -> tuple[list[ScheduledRecording], list[str]]:
    """Load scheduled recordings from a JSON file, dropping any whose
    stop time has already passed -- there's no reason to keep re-checking
    or displaying an expired entry. A missing file is not an error;
    malformed entries are reported as warning strings and skipped rather
    than raising."""
    if not path.is_file():
        return [], []

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f"Could not read schedule file {path}: {exc}"]

    if not isinstance(data, list):
        return [], [f"Schedule file {path} must contain a JSON array of entries"]

    now = datetime.now(timezone.utc)
    schedules: list[ScheduledRecording] = []
    warnings: list[str] = []
    for index, entry in enumerate(data):
        try:
            start = datetime.fromisoformat(entry["start"])
            stop = datetime.fromisoformat(entry["stop"])
            recording = ScheduledRecording(
                id=entry["id"],
                channel_url=entry["channel_url"],
                channel_name=entry["channel_name"],
                title=entry["title"],
                start=start,
                stop=stop,
            )
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"Ignoring malformed schedule entry {index} in {path}: {exc}")
            continue
        if recording.stop <= now:
            continue
        schedules.append(recording)
    return schedules, warnings


def save_schedule(path: Path, schedules: list[ScheduledRecording]) -> None:
    """Write scheduled recordings back to their JSON file. Creates the
    parent directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "id": s.id,
            "channel_url": s.channel_url,
            "channel_name": s.channel_name,
            "title": s.title,
            "start": s.start.isoformat(),
            "stop": s.stop.isoformat(),
        }
        for s in schedules
    ]
    path.write_text(json.dumps(data, indent=2) + "\n")
