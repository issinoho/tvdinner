"""GUI entry point for the macOS app bundle.

Unlike the Windows build (which stays a console app, launched from a
shell with a URL argument -- see windows/tvdinner_entry.py), a
double-clicked .app has no terminal to pass an argument to. This
prompts for the M3U/stream URL instead (pre-filled with the last one
used), then hands off to the same tvdinner.cli.main() every other
platform uses.

PyInstaller needs a real, analyzable .py script for its Analysis --
not the `console_scripts` entry point tvdinner installs normally --
same reason windows/tvdinner_entry.py exists.
"""

from __future__ import annotations

import ctypes.util
import glob
import os
import sys


def _load_last_url() -> str:
    from tvdinner.epg import DEFAULT_CHANNEL_SHIFTS_PATH

    last_url_path = DEFAULT_CHANNEL_SHIFTS_PATH.parent / "last_url.txt"
    try:
        return last_url_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _save_last_url(url: str) -> None:
    from tvdinner.epg import DEFAULT_CHANNEL_SHIFTS_PATH

    last_url_path = DEFAULT_CHANNEL_SHIFTS_PATH.parent / "last_url.txt"
    try:
        last_url_path.parent.mkdir(parents=True, exist_ok=True)
        last_url_path.write_text(url + "\n", encoding="utf-8")
    except OSError:
        pass  # best-effort -- not worth failing playback over


def _patch_bundled_libmpv() -> None:
    """Inside a PyInstaller-frozen .app, libmpv ships right next to the
    executable (see tvdinner.spec's `binaries=`), not in any location
    python-mpv's own ctypes.util.find_library('mpv') would ever search
    -- point it there before tvdinner.player (which does `import mpv`
    at module load time) is imported anywhere below. A no-op when run
    unfrozen (e.g. `python macos/tvdinner_entry.py <url>` during
    development), where the real find_library still works normally.
    """
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if not bundle_dir:
        return

    candidates = glob.glob(os.path.join(bundle_dir, "libmpv*.dylib"))
    if not candidates:
        return
    bundled = candidates[0]

    original_find_library = ctypes.util.find_library

    def _find_library(name: str):
        if name == "mpv":
            return bundled
        return original_find_library(name)

    ctypes.util.find_library = _find_library


def _prompt_for_url(default: str) -> str | None:
    """Show a native text-entry dialog pre-filled with `default`.
    Returns None if the user cancelled or left it empty."""
    import tkinter
    from tkinter import simpledialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # else the dialog can open behind other windows on first launch
    url = simpledialog.askstring(
        "tvdinner",
        "M3U playlist URL/path, or a direct stream URL:",
        initialvalue=default,
        parent=root,
    )
    root.destroy()
    return url.strip() if url and url.strip() else None


def main() -> int:
    _patch_bundled_libmpv()  # must run before `from tvdinner import cli` below
    from tvdinner import cli

    url = _prompt_for_url(_load_last_url())
    if not url:
        return 0
    _save_last_url(url)
    return cli.main([url])


if __name__ == "__main__":
    sys.exit(main())
