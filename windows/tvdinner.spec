# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

Run from the repo root as:
    pyinstaller windows/tvdinner.spec

Expects libmpv-2.dll to already be sitting at windows/build/libmpv-2.dll
(see .github/workflows/release.yml's build-windows job, which downloads
it from https://github.com/shinchiro/mpv-winbuild-cmake). python-mpv
looks for mpv-2.dll/libmpv-2.dll/mpv-1.dll next to the running
executable before anywhere else on Windows, so bundling it at the dist
root (not inside a subfolder) is what lets tvdinner.exe find it with no
extra PATH setup.
"""

import os

repo_root = os.path.dirname(SPECPATH)
fonts_dir = os.path.join(repo_root, "src", "tvdinner", "fonts")
libmpv_dll = os.path.join(repo_root, "windows", "build", "libmpv-2.dll")

a = Analysis(
    [os.path.join(SPECPATH, "tvdinner_entry.py")],
    pathex=[os.path.join(repo_root, "src")],
    binaries=[(libmpv_dll, ".")],
    datas=[
        (os.path.join(fonts_dir, "Inter-Regular.ttf"), "tvdinner/fonts"),
        (os.path.join(fonts_dir, "Inter-Bold.ttf"), "tvdinner/fonts"),
    ],
    hiddenimports=[],
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
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="tvdinner",
)
