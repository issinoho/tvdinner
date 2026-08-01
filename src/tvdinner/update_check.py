"""Check GitHub Releases for a newer tvdinner version.

tvdinner is distributed four different ways (.deb, .rpm, a Windows
installer, a macOS .dmg), each with its own update mechanics -- a Windows
installer can cleanly self-upgrade in place, but .deb/.rpm need root and
have no hosted repo, and the macOS .dmg has no fixed install location and
is unsigned. Silently replacing files is realistically only safe on
Windows, so this module deliberately does not attempt that anywhere: it
only checks whether a newer release exists and hands back the release
page URL for the caller to open in a browser -- the user finishes the
actual install their normal way, on every platform alike.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import requests

if sys.platform == "win32":
    DEFAULT_UPDATE_CHECK_PATH = Path(os.environ.get("APPDATA", Path.home())) / "tvdinner" / "update_check.json"
elif sys.platform == "darwin":
    DEFAULT_UPDATE_CHECK_PATH = Path.home() / "Library" / "Application Support" / "tvdinner" / "update_check.json"
else:
    DEFAULT_UPDATE_CHECK_PATH = Path.home() / ".config" / "tvdinner" / "update_check.json"

_RELEASES_LATEST_URL = "https://api.github.com/repos/issinoho/tvdinner/releases/latest"

_DEFAULT_CHECK_INTERVAL = timedelta(hours=24)


@dataclass
class UpdateCheckState:
    last_checked: datetime | None = None
    skipped_version: str | None = None


def load_update_check_state(path: Path) -> tuple[UpdateCheckState, list[str]]:
    """Load persisted update-check state (when it last checked, and any
    version the user has already seen and dismissed). A missing file is
    not an error -- it just means it's never checked before. Malformed
    JSON is reported as a warning rather than raising, so a corrupt file
    just means checking again now rather than blocking startup."""
    if not path.is_file():
        return UpdateCheckState(), []

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return UpdateCheckState(), [f"Could not read update-check file {path}: {exc}"]

    if not isinstance(data, dict):
        return UpdateCheckState(), [f"Update-check file {path} must contain a JSON object"]

    last_checked_raw = data.get("last_checked")
    last_checked = None
    if isinstance(last_checked_raw, str):
        try:
            last_checked = datetime.fromisoformat(last_checked_raw)
        except ValueError:
            pass

    skipped_version = data.get("skipped_version")
    if not isinstance(skipped_version, str):
        skipped_version = None

    return UpdateCheckState(last_checked=last_checked, skipped_version=skipped_version), []


def save_update_check_state(path: Path, state: UpdateCheckState) -> None:
    """Write update-check state back to its JSON file. Creates the parent
    directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_checked": state.last_checked.isoformat() if state.last_checked else None,
        "skipped_version": state.skipped_version,
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def should_check_now(state: UpdateCheckState, now: datetime, interval: timedelta = _DEFAULT_CHECK_INTERVAL) -> bool:
    """Whether enough time has passed since the last check to check again
    -- keeps a single user nowhere near GitHub's 60-requests-per-hour
    unauthenticated rate limit regardless of how often tvdinner itself is
    launched."""
    return state.last_checked is None or now - state.last_checked >= interval


def _parse_version(version: str) -> tuple[tuple[int, ...], int]:
    """tvdinner's version scheme is `X.Y.Z-N` (N an ever-incrementing
    release counter) -- not real semver, so a plain string compare gets
    `0.1.0-100` and `0.1.0-99` backwards (lexicographic, not numeric).
    Splits on the last '-' and compares both halves as integers instead.
    Tuple comparison also correctly handles a hypothetical future prefix
    bump (e.g. 0.1.0 -> 0.2.0), falling through to compare that first."""
    version = version.removeprefix("v")
    prefix, _, counter = version.rpartition("-")
    return tuple(int(part) for part in prefix.split(".")), int(counter)


def is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


@dataclass
class UpdateInfo:
    version: str  # e.g. "0.1.0-93", no leading "v"
    html_url: str  # the GitHub release page to open


def check_for_update(current_version: str, timeout: float = 10) -> tuple[UpdateInfo | None, str | None]:
    """Check GitHub Releases for a version newer than `current_version`.
    Returns (None, None) if already up to date, (UpdateInfo, None) if a
    newer release exists, or (None, message) on a network/parse failure
    -- never raises, matching every other network loader in this app.
    /releases/latest already excludes drafts and prereleases, so no
    further filtering is needed here."""
    try:
        response = requests.get(
            _RELEASES_LATEST_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"tvdinner/{current_version}"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        return None, f"Could not reach GitHub: {exc}"
    except ValueError as exc:
        return None, f"GitHub returned an unexpected response: {exc}"

    tag_name = data.get("tag_name") if isinstance(data, dict) else None
    html_url = data.get("html_url") if isinstance(data, dict) else None
    if not isinstance(tag_name, str) or not isinstance(html_url, str):
        return None, "GitHub release response was missing tag_name/html_url"

    remote_version = tag_name.removeprefix("v")
    if not is_newer(remote_version, current_version):
        return None, None

    return UpdateInfo(version=remote_version, html_url=html_url), None
