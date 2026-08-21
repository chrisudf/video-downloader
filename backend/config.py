from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from .appdirs import is_frozen, user_data_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Source runs keep config.json next to the code (unchanged behaviour).
# Frozen builds must not write into the install dir — an .app in
# /Applications or a Program Files install is read-only for the user — so
# config lives in the per-user data dir instead.
CONFIG_PATH = (user_data_dir() / "config.json") if is_frozen() else (PROJECT_ROOT / "config.json")

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
    # First-run bootstrap: download missing yt-dlp / N_m3u8DL-RE / ffmpeg
    # into the per-user tools dir automatically. Set false to manage tools
    # yourself.
    "auto_download_tools": True,
    # YouTube only. Empty string = yt-dlp's own client selection, which is
    # the right default now that the app installs a nightly yt-dlp and a JS
    # runtime (both of which the defaults assume). Kept as a knob because
    # client viability is a moving, per-network target: the clean-IP re-test
    # the previous "web" default asked for showed the opposite result there
    # ("web" returned zero formats, the default worked) — no pinned value is
    # right everywhere. Values worth trying on a failing network: "web",
    # "web_safari", "tv". Don't list several: yt-dlp merges their format
    # lists and a selector then matches a format from a client that cannot
    # serve it.
    "youtube_player_client": "",
    # JavaScript runtime for yt-dlp's YouTube challenge solving. yt-dlp only
    # enables deno by default, so a machine with just node needs this named
    # explicitly or the good formats are never offered.
    #
    # Accepts a bare name ("deno"), an absolute path to the executable, or
    # yt-dlp's own "name:path" spelling. Empty = auto-detect deno/node/bun on
    # PATH, and let first-run bootstrap install deno if none is found.
    "js_runtime": "",
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
        # Atomic write: the frozen app rewrites config repeatedly (bootstrap
        # writes the tool paths, every settings save writes again). A crash
        # or power loss mid-write must never leave a truncated file — a
        # corrupt config.json would otherwise fail json.loads at import time
        # on every subsequent launch.
        import os
        import uuid

        tmp = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)

    def update(self, patch: dict[str, Any]) -> None:
        patch = dict(patch)
        # /api/config accepts arbitrary JSON, so re-validate rather than
        # trusting whatever the caller sent.
        if "custom_headers" in patch:
            patch["custom_headers"] = normalize_headers(patch["custom_headers"])
        self._data.update(patch)
        self.save()


def load_config() -> Config:
    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("config.json is not a JSON object")
        except (ValueError, OSError):
            # A corrupt config must degrade to defaults, never prevent
            # startup (in the windowed build the crash would be invisible).
            # Keep the evidence aside for debugging.
            try:
                CONFIG_PATH.replace(CONFIG_PATH.with_suffix(".json.bad"))
            except OSError:
                pass
            data = {}
    return Config(data)


config = load_config()


JS_RUNTIME_NAMES = ("deno", "node", "bun")


def parse_js_runtime(value: Any) -> Optional[tuple[str, Optional[str]]]:
    """Interpret the js_runtime setting as (runtime_name, explicit_path).

    Three accepted spellings, because all three are things a user will
    plausibly type:

        "deno"                     -> ("deno", None)
        "C:/Program Files/nodejs/node.exe" -> ("node", "C:/.../node.exe")
        "node:/usr/bin/node"       -> ("node", "/usr/bin/node")

    A bare path cannot be handed to yt-dlp as-is: --js-runtimes takes
    RUNTIME[:PATH], so "/usr/bin/node" would be read as the name of an
    unsupported runtime. The name is recovered from the filename instead.

    Returns None when empty or when the runtime can't be identified — the
    caller then falls back to auto-detection rather than passing yt-dlp a
    flag it will reject.
    """
    text = str(value or "").strip()
    if not text:
        return None
    # "name:path" — checked first and anchored to a known name, so a bare
    # Windows path keeps its drive-letter colon instead of being split on it.
    for name in JS_RUNTIME_NAMES:
        prefix = f"{name}:"
        if text.lower().startswith(prefix):
            path = text[len(prefix):].strip()
            return (name, path or None)
    if text.lower() in JS_RUNTIME_NAMES:
        return (text.lower(), None)
    # Otherwise treat it as a path and recover the runtime from the filename.
    stem = Path(text).stem.lower()
    if stem in JS_RUNTIME_NAMES:
        return (stem, text)
    return None


def format_js_runtime(parsed: tuple[str, Optional[str]]) -> str:
    """Render (name, path) back into yt-dlp's --js-runtimes argument."""
    name, path = parsed
    return f"{name}:{path}" if path else name
