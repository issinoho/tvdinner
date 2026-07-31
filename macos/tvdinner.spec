# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS build.

Run from the repo root as:
    pyinstaller macos/tvdinner.spec

Expects two files to already exist (see .github/workflows/release.yml's
build-macos job):
  - macos/build/libmpv.dylib -- copied from Homebrew's `mpv` formula.
    Bundled at the dist root (not a subfolder) and pointed to explicitly
    by tvdinner_entry.py's _patch_bundled_libmpv() before `import mpv`
    ever runs, since python-mpv's own ctypes.util.find_library('mpv')
    has no reason to look inside an app bundle.
  - macos/build/tvdinner.icns -- generated from docs/assets/icon-512.png.

Unlike the Windows build (a console app, `console=True`), this is a
double-clicked GUI app: no terminal window, and tvdinner_entry.py itself
prompts for the playlist URL via Tkinter instead of taking it as an
argv argument.
"""

import os
import sys

repo_root = os.path.dirname(SPECPATH)
sys.path.insert(0, os.path.join(repo_root, "src"))
from tvdinner import __version__  # noqa: E402

fonts_dir = os.path.join(repo_root, "src", "tvdinner", "fonts")
libmpv_dylib = os.path.join(repo_root, "macos", "build", "libmpv.dylib")
icon_file = os.path.join(repo_root, "macos", "build", "tvdinner.icns")

a = Analysis(
    [os.path.join(SPECPATH, "tvdinner_entry.py")],
    pathex=[os.path.join(repo_root, "src")],
    binaries=[(libmpv_dylib, ".")],
    datas=[
        (os.path.join(fonts_dir, "Inter-Regular.ttf"), "tvdinner/fonts"),
        (os.path.join(fonts_dir, "Inter-Bold.ttf"), "tvdinner/fonts"),
    ],
    hiddenimports=["tkinter"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tvdinner",
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="tvdinner",
)

app = BUNDLE(
    coll,
    name="tvdinner.app",
    icon=icon_file,
    bundle_identifier="com.issinoho.tvdinner",
    info_plist={
        "CFBundleName": "tvdinner",
        "CFBundleDisplayName": "tvdinner",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
