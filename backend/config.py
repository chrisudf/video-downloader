from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

_DEFAULTS: dict[str, Any] = {
    "save_dir": str(Path.home() / "Downloads"),
    "ytdlp_path": "yt-dlp",
    "m3u8dl_path": "N_m3u8DL-RE",
    "ffmpeg_path": "ffmpeg",
    "port": 8765,
    "max_concurrent_downloads": 2,
}


class Config:
    def __init__(self, data: dict[str, Any]):
        self._data = {**_DEFAULTS, **data}

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
