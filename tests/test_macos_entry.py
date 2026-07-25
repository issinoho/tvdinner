"""Tests for macos/tvdinner_entry.py's non-GUI logic (persisting the
last-used URL, and patching ctypes.util.find_library to a bundled
libmpv.dylib). The actual Tkinter prompt isn't testable here -- it
needs a real macOS build/launch to verify, per the plan this was built
against.
"""

import ctypes.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "macos"))
import tvdinner_entry as entry  # noqa: E402


def test_load_last_url_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.epg.DEFAULT_CHANNEL_SHIFTS_PATH", tmp_path / "epg_shifts.json")
    assert entry._load_last_url() == ""


def test_save_and_load_last_url_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("tvdinner.epg.DEFAULT_CHANNEL_SHIFTS_PATH", tmp_path / "epg_shifts.json")
    entry._save_last_url("https://example.com/playlist.m3u")
    assert entry._load_last_url() == "https://example.com/playlist.m3u"


def test_patch_bundled_libmpv_noop_when_not_frozen(monkeypatch):
    monkeypatch.setattr(ctypes.util, "find_library", ctypes.util.find_library)  # registers a restore point
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    original = ctypes.util.find_library
    entry._patch_bundled_libmpv()
    assert ctypes.util.find_library is original


def test_patch_bundled_libmpv_redirects_mpv_lookup_only(tmp_path, monkeypatch):
    monkeypatch.setattr(ctypes.util, "find_library", ctypes.util.find_library)  # registers a restore point
    fake_dylib = tmp_path / "libmpv.2.dylib"
    fake_dylib.write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    entry._patch_bundled_libmpv()

    assert ctypes.util.find_library("mpv") == str(fake_dylib)
    assert ctypes.util.find_library("c") is not None  # other lookups still work normally


def test_patch_bundled_libmpv_noop_when_no_dylib_present(tmp_path, monkeypatch):
    monkeypatch.setattr(ctypes.util, "find_library", ctypes.util.find_library)  # registers a restore point
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)  # empty dir, no libmpv* file
    original = ctypes.util.find_library

    entry._patch_bundled_libmpv()

    assert ctypes.util.find_library is original
