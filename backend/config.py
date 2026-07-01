from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

# Cross-platform defaults. On Windows, executables typically need ".exe" and
# may live at well-known absolute paths. On macOS / Linux they're usually on
# PATH after `brew install` / `pip install`.
_IS_WIN = sys.platform == "win32"

_DEFAULTS: dict[str, Any] = {
    "save_dir": str(Path.home() / "Downloads" / "VideoDownloader"),
    "ytdlp_path": "yt-dlp.exe" if _IS_WIN else "yt-dlp",
    "m3u8dl_path": "N_m3u8DL-RE.exe" if _IS_WIN else "N_m3u8DL-RE",
    "ffmpeg_path": "ffmpeg.exe" if _IS_WIN else "ffmpeg",
    "port": 8765,
    "max_concurrent_downloads": 2,
    # YouTube's "Sign in to confirm you're not a bot" check needs cookies for
    # many videos. Set to "chrome" / "edge" / "firefox" / "brave" / "opera" /
    # "safari" (macOS) / "chromium" to auto-import cookies from that browser
    # profile. Leave empty to try without cookies first.
    #
    # On modern Windows Chrome (127+) uses app-bound encryption that yt-dlp
    # cannot decrypt — install Firefox, or export cookies with a browser
    # extension and set `youtube_cookies_file` below to that file's path.
    "youtube_cookies_from": "chrome",
    # Optional: absolute path to a Netscape/Mozilla-format cookies.txt file.
    # Wins over youtube_cookies_from when set.
    "youtube_cookies_file": "",
}


def _expand(value: Any) -> Any:
    """Expand ~ and environment variables in any path-shaped string."""
    if isinstance(value, str) and value:
        # Treat as path if it contains ~ or env-var syntax; otherwise pass through
        if value.startswith("~") or "$" in value or "%" in value:
            return str(Path(value).expanduser())
    return value


class Config:
    def __init__(self, data: dict[str, Any]):
        merged = {**_DEFAULTS, **data}
        # Expand ~ in any path-like value so users can write "~/Videos" in config.json
        for k in ("save_dir", "ytdlp_path", "m3u8dl_path", "ffmpeg_path", "youtube_cookies_file"):
            if k in merged:
                merged[k] = _expand(merged[k])
        self._data = merged

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def update(self, patch: dict[str, Any]) -> None:
        self._data.update(patch)
        self.save()


def load_config() -> Config:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        data = {}
    return Config(data)


config = load_config()
