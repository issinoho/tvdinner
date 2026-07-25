"""Backup/restore for tvdinner's configuration files (EPG shifts,
favorites, bookmarks) as a single compressed zip archive, for offline
storage or moving to another machine.

The EPG cache and log file are deliberately excluded -- they're
disposable/re-fetchable operational data, not configuration a user has
curated by hand.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def create_backup(output_path: Path, config_paths: dict[str, Path]) -> list[str]:
    """Write a zip archive at `output_path` containing whichever of
    `config_paths` (arcname -> path) currently exist. Returns the
    arcnames actually included -- a config file that doesn't exist yet
    (e.g. no favorites saved) is skipped, not an error. Creates the
    parent directory of `output_path` if needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    included: list[str] = []
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for arcname, path in config_paths.items():
            if path.is_file():
                archive.write(path, arcname=arcname)
                included.append(arcname)
    return included


def restore_backup(input_path: Path, config_paths: dict[str, Path]) -> tuple[list[str], list[str]]:
    """Extract each entry of the zip archive at `input_path` that matches
    a name in `config_paths` (arcname -> path) to that path, overwriting
    it and creating its parent directory if needed. Returns (restored
    arcnames, unrecognized arcnames in the archive that were skipped --
    e.g. from a newer tvdinner version with an extra config file)."""
    restored: list[str] = []
    unknown: list[str] = []
    with zipfile.ZipFile(input_path, "r") as archive:
        for arcname in archive.namelist():
            path = config_paths.get(arcname)
            if path is None:
                unknown.append(arcname)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(arcname) as source, path.open("wb") as dest:
                dest.write(source.read())
            restored.append(arcname)
    return restored, unknown
