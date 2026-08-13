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
from logging.handlers import RotatingFileHandler
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_LOG_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "tvdinner" / "tvdinner.log"
else:
    DEFAULT_LOG_PATH = Path.home() / ".cache" / "tvdinner" / "tvdinner.log"

# Every session appends indefinitely otherwise (see configure_logging's own
# note that it's the only record of a session once mpv's window closes) --
# capped at 5MB with one rotated backup (tvdinner.log.1) so it can't grow
# without bound across months of daily use.
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 1


def configure_logging(log_path: Path | None, level: int = logging.INFO) -> None:
    """Attach a file handler to the root logger so every module's logger
    lands in the same file, and route Python's own `warnings.warn()` calls
    (e.g. python-mpv's event-loop warnings) through it too. A no-op if
    `log_path` is None (e.g. --no-log).

    Safe to call more than once for the same path -- e.g. `tvdinner
    bookmarks` configures logging itself, then re-enters main() (which
    also calls this) when a bookmark is launched; without this check
    that would attach a second handler and double-write every line."""
    if log_path is None:
        return
    root = logging.getLogger()
    resolved = os.path.abspath(os.fspath(log_path))
    if any(isinstance(h, logging.FileHandler) and h.baseFilename == resolved for h in root.handlers):
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.setLevel(level)
    root.addHandler(handler)
    logging.captureWarnings(True)


def close_logging(log_path: Path | None) -> None:
    """Detach and close the file handler configure_logging attached for
    `log_path`, if any -- for a caller that needs to delete or otherwise
    touch the log file itself out from under a still-running process (see
    cli.py's hard-reset command) rather than just stop routing new lines
    to it: configure_logging(None) is deliberately a no-op (see its own
    docstring), so it can't be reused for this. Safe to call even if
    nothing was ever configured for this path."""
    if log_path is None:
        return
    root = logging.getLogger()
    resolved = os.path.abspath(os.fspath(log_path))
    for handler in list(root.handlers):
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == resolved:
            root.removeHandler(handler)
            handler.close()
