"""File-based logging setup, shared across the app.

mpv owns the actual window and a live terminal is often not watched once
playback starts, so this is the only record of what happened in a session
(startup/shutdown, every user-triggered action, warnings and errors) once
it's over.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_LOG_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "tvdinner" / "tvdinner.log"
else:
    DEFAULT_LOG_PATH = Path.home() / ".cache" / "tvdinner" / "tvdinner.log"


def configure_logging(log_path: Path | None, level: int = logging.INFO) -> None:
    """Attach a file handler to the root logger so every module's logger
    lands in the same file, and route Python's own `warnings.warn()` calls
    (e.g. python-mpv's event-loop warnings) through it too. A no-op if
    `log_path` is None (e.g. --no-log)."""
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    logging.captureWarnings(True)
