"""Where the app keeps its files, source-run and frozen (PyInstaller) alike.

Two distinct roots, never to be confused:

- resource_root(): read-only files shipped with the app (frontend/, defaults).
  In a frozen build this lives inside the install/bundle directory and must
  never be written to (on macOS an .app in /Applications is not writable;
  on Windows the install dir may be admin-owned).
- user_data_dir(): per-user writable state (config.json, downloaded tools,
  logs). Survives app upgrades and reinstalls.

VD_DATA_DIR overrides user_data_dir() — used by tests and by anyone who
wants a portable install.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "VideoDownloader"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Directory containing bundled read-only resources (frontend/ etc.)."""
    if is_frozen():
        # PyInstaller extracts data files under _MEIPASS (onefile: temp dir;
        # onedir: the _internal directory next to the executable).
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Per-user writable directory for config, tools and logs. Created on
    first call so callers can write into it without their own mkdir."""
    override = os.environ.get("VD_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(root) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(root) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def managed_tools_dir() -> Path:
    """Where auto-downloaded helper binaries (yt-dlp, N_m3u8DL-RE, ffmpeg)
    are installed."""
    d = user_data_dir() / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = user_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d
