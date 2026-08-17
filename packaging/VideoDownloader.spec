# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — one spec for Windows and macOS.
#
#   Windows:  .build_venv\Scripts\python.exe -m PyInstaller packaging\VideoDownloader.spec --noconfirm
#   macOS:    .build_venv/bin/pyinstaller packaging/VideoDownloader.spec --noconfirm
#
# onedir (not onefile): starts instantly, updates diff better, and avoids the
# antivirus heuristics that flag self-extracting onefile binaries.
# Set VD_CONSOLE=1 to build a console-attached debug binary.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # packaging/ -> repo root
CONSOLE = bool(os.environ.get("VD_CONSOLE"))

hiddenimports = [
    # uvicorn picks its loop/protocol/lifespan classes from strings at
    # runtime; static analysis can't see them.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
# yt_dlp is the last-resort probe fallback; its ~1000 extractors are loaded
# lazily via importlib, invisible to static analysis.
hiddenimports += collect_submodules("yt_dlp")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "frontend"), "frontend"),
        (str(ROOT / "config.example.json"), "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # uvloop unused (we pass loop="asyncio"); tkinter dragged in by stdlib.
    excludes=["uvloop", "tkinter", "_tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="VideoDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VideoDownloader",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="VideoDownloader.app",
        icon=None,
        bundle_identifier="com.chrisudf.videodownloader",
        info_plist={
            "CFBundleName": "Video Downloader",
            "CFBundleDisplayName": "Video Downloader",
            "CFBundleShortVersionString": "0.2.0",
            "NSHighResolutionCapable": True,
            # The app has no windows of its own (UI is the browser tab), but
            # keep it in the Dock so users can see it's running and quit it.
            "LSUIElement": False,
        },
    )
