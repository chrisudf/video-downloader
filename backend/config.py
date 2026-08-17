from __future__ import annotations

import json
import re
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
    # Extra HTTP headers applied to every probe/download. Streams behind
    # hotlink protection, private/self-hosted servers and login-gated
    # platforms typically need a Referer, a specific User-Agent or a Cookie.
    "custom_headers": {},
}

# Header names are restricted to RFC 7230 token characters. This is not
# cosmetic: both downloaders serialise headers into "Name: Value" CLI
# arguments, so a name containing ":" or CR/LF would let one entry forge
# additional headers (or corrupt the argument entirely).
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def canonical_header_name(name: str) -> str:
    """Title-case a header name the way HTTP conventionally writes it
    ("user-agent" -> "User-Agent"). Header names are case-insensitive, so
    every consumer has to agree on one spelling: otherwise a user-configured
    "referer" is invisible to headers.get("Referer"), survives a dict merge
    as a separate key, and gets serialised as a second, conflicting header."""
    return "-".join(p[:1].upper() + p[1:].lower() for p in name.split("-"))


def normalize_headers(value: Any) -> dict[str, str]:
    """Coerce a config/user-supplied header mapping into a safe dict, with
    names canonicalised — which also de-duplicates them case-insensitively,
    last entry winning, matching plain dict-merge semantics.
    Silently drops entries that aren't usable rather than raising — this runs
    on the /api/config path where a bad entry shouldn't brick the app."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for name, val in value.items():
        if not isinstance(name, str) or not isinstance(val, (str, int, float)):
            continue
        name = name.strip()
        val = str(val).strip()
        if not name or not val:
            continue
        if not _HEADER_NAME_RE.match(name):
            continue
        # Strip anything that could terminate the header/argument.
        if any(c in val for c in ("\r", "\n", "\0")):
            continue
        out[canonical_header_name(name)] = val
    return out


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
        for k in ("save_dir", "ytdlp_path", "m3u8dl_path", "ffmpeg_path"):
            if k in merged:
                merged[k] = _expand(merged[k])
        merged["custom_headers"] = normalize_headers(merged.get("custom_headers"))
        self._data = merged

    def headers(self) -> dict[str, str]:
        """The configured extra headers, safe to hand to a downloader."""
        return dict(self._data.get("custom_headers") or {})

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
        patch = dict(patch)
        # /api/config accepts arbitrary JSON, so re-validate rather than
        # trusting whatever the caller sent.
        if "custom_headers" in patch:
            patch["custom_headers"] = normalize_headers(patch["custom_headers"])
        self._data.update(patch)
        self.save()


def load_config() -> Config:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        data = {}
    return Config(data)


config = load_config()
