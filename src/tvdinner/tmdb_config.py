"""Persistence for a global default TMDB API token (see `tvdinner
store-tmdb`/`tvdinner clear-tmdb`) -- a fallback used whenever
--tmdb-api-token isn't given directly, so a frequently-used token doesn't
need retyping on every invocation. An explicit --tmdb-api-token always
overrides this, including one carried by a bookmark's own saved token
(see cli.py's run_bookmarks_command, which funnels that through as the
same flag when re-entering main()) -- this module knows nothing about
that precedence itself, it's just the storage.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    DEFAULT_TMDB_TOKEN_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "tmdb_token.json"
else:
    DEFAULT_TMDB_TOKEN_PATH = Path.home() / ".config" / "tvdinner" / "tmdb_token.json"


def load_tmdb_token(path: Path) -> tuple[str | None, list[str]]:
    """(token, warnings). A missing file, or one with no token saved yet,
    isn't an error -- it just means there's no stored default. Malformed
    JSON or a malformed entry is reported as a warning string rather than
    raising, matching every other load_*/save_* pair in this codebase."""
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"Could not read stored TMDB token file {path}: {exc}"]
    if not isinstance(data, dict):
        return None, [f"TMDB token file {path} must contain a JSON object"]
    token = data.get("tmdb_api_token")
    if token is not None and not isinstance(token, str):
        return None, [f"TMDB token file {path}'s tmdb_api_token is not a string; ignoring"]
    return (token or None), []


def save_tmdb_token(path: Path, token: str) -> None:
    """Persist `token` as tvdinner's global default TMDB API token,
    overwriting any previously stored one. Creates the parent directory
    if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tmdb_api_token": token}, indent=2) + "\n")
    try:
        path.chmod(0o600)  # a real credential; best-effort, matches gdrive.py's own token file
    except OSError:
        pass  # not every filesystem supports it


def clear_tmdb_token(path: Path) -> bool:
    """Remove the stored token file, if any. Returns whether a file was
    actually removed, so a caller can report "nothing to clear"
    separately from "removed"."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
