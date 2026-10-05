"""First-run bootstrap: make the three external tools appear without the user
touching a terminal.

Non-developers can't be asked to `brew install ffmpeg` or fish a binary out of
a GitHub release page. Instead, on startup (and on demand from the UI) this
module downloads any missing tool into the app's per-user tools directory and
points config.json at the absolute path. Everything is verified (magic header
+ an actual run) before it is adopted, mirroring tools._download_latest_ytdlp.

Progress is exposed as a plain dict (get_status) that the frontend polls, so
the user sees "downloading ffmpeg 43%" instead of a dead page.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from . import tools
from .appdirs import managed_tools_dir
from .config import JS_RUNTIME_NAMES, config, format_js_runtime, parse_js_runtime

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def _machine_arch() -> str:
    """'arm64' or 'x64' — normalised across platforms."""
    import platform

    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    return "x64"


# ---------------------------------------------------------------------------
# Download sources.
#
# yt-dlp publishes fixed-name assets, so /releases/latest/download/ works.
# N_m3u8DL-RE embeds the version + build date in asset names, so we resolve
# the latest tag via the (unauthenticated, un-rate-limited) redirect of
# /releases/latest, then scrape the expanded_assets HTML fragment for the
# platform's asset URL. The GitHub REST API is only a fallback because
# anonymous calls are capped at 60/hour per IP.
# ffmpeg has no official binaries; these are the community static builds that
# yt-dlp's own docs point at (BtbN for Windows) plus the two established
# macOS static-build sites (martin-riedl.de publishes arm64+x64 with stable
# "latest" URLs; evermeet.cx is the long-standing x64 source).
# ---------------------------------------------------------------------------

_YTDLP_ASSET = {
    "win32": "yt-dlp.exe",
    "darwin": "yt-dlp_macos",
}.get(sys.platform, "yt-dlp")

_M3U8DL_REPO = "nilaoda/N_m3u8DL-RE"


def _m3u8dl_platform_key() -> str:
    if IS_WIN:
        return "win-x64"
    if IS_MAC:
        return f"osx-{_machine_arch()}"
    return f"linux-{_machine_arch().replace('x64', 'x64')}"


_FFMPEG_WIN_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
    "ffmpeg-master-latest-win64-gpl.zip"
)
# martin-riedl.de: stable redirect to the latest release build per platform.
_FFMPEG_MAC_URLS = {
    "arm64": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip",
    "x64": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffmpeg.zip",
}
_FFMPEG_MAC_FALLBACK = "https://evermeet.cx/ffmpeg/getrelease/zip"  # x86_64 only

# deno: a JS runtime for yt-dlp's YouTube challenge solving. yt-dlp enables
# only deno by default, so it is the runtime to install when none is present.
# Fixed-name assets, so /releases/latest/download/ resolves without hitting
# the rate-limited REST API.
def _deno_asset() -> str:
    arch = _machine_arch()
    if IS_WIN:
        return "deno-x86_64-pc-windows-msvc.zip"
    if IS_MAC:
        return "deno-aarch64-apple-darwin.zip" if arch == "arm64" else "deno-x86_64-apple-darwin.zip"
    return (
        "deno-aarch64-unknown-linux-gnu.zip" if arch == "arm64"
        else "deno-x86_64-unknown-linux-gnu.zip"
    )


# ---------------------------------------------------------------------------
# Status shared with the frontend
# ---------------------------------------------------------------------------

_status: dict[str, Any] = {
    "state": "idle",  # idle | running | done | error
    "steps": {},       # tool -> {state, message, percent}
    "error": None,
}
_lock = asyncio.Lock()


def get_status() -> dict[str, Any]:
    out = dict(_status)
    out["steps"] = {k: dict(v) for k, v in _status["steps"].items()}
    out["missing"] = missing_tools()
    return out


def _step(tool: str, state: str, message: str = "", percent: Optional[float] = None) -> None:
    entry = _status["steps"].setdefault(tool, {})
    entry["state"] = state
    entry["message"] = message
    if percent is not None:
        entry["percent"] = round(percent, 1)


# ---------------------------------------------------------------------------
# PATH augmentation + detection
# ---------------------------------------------------------------------------

def augment_path() -> None:
    """Prepend the managed tools dir (and, on macOS, the Homebrew dirs) to
    PATH for this process. A .app launched from Finder inherits a minimal
    PATH without /opt/homebrew/bin, so a brew-installed ffmpeg would be
    invisible without this. Must run before anything calls shutil.which."""
    extras = [str(managed_tools_dir())]
    if IS_MAC:
        extras += ["/opt/homebrew/bin", "/usr/local/bin"]
    if not IS_WIN:
        extras.append(str(Path.home() / ".local" / "bin"))
    current = os.environ.get("PATH", "")
    parts = [p for p in extras if p and Path(p).is_dir() and p not in current.split(os.pathsep)]
    if parts:
        os.environ["PATH"] = os.pathsep.join(parts + [current])


def missing_tools() -> list[str]:
    """Which of the managed tools can't currently be resolved."""
    out = []
    for name, cfg_path in (
        ("ytdlp", config.ytdlp_path),
        ("m3u8dl", config.m3u8dl_path),
        ("ffmpeg", config.ffmpeg_path),
    ):
        if not isinstance(cfg_path, str) or not cfg_path or shutil.which(cfg_path) is None:
            out.append(name)
    if not _resolve_js_runtime():
        out.append("jsruntime")
    return out


def _resolve_js_runtime() -> Optional[str]:
    """The JS runtime yt-dlp will be able to use, or None.

    Counted as a managed tool because YouTube extraction now depends on it:
    without a runtime the usable formats are never listed, so reporting
    first-run setup as complete without one would leave a packaged machine
    with YouTube quietly broken.
    """
    parsed = parse_js_runtime(getattr(config, "js_runtime", ""))
    if parsed:
        name, path = parsed
        # A configured entry only counts if it actually resolves — a stale
        # path left behind by an uninstall must not suppress the download.
        if (path and Path(path).exists()) or (not path and shutil.which(name)):
            return format_js_runtime(parsed)
    for name in JS_RUNTIME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _http_client():
    import httpx

    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(300.0, connect=20.0),
        # Browser-like UA: ffmpeg.martin-riedl.de answers 403 to non-browser
        # user agents; GitHub doesn't care either way.
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )


def _download_to(url: str, dest: Path, progress: Callable[[float], None]) -> None:
    """Stream url to dest (blocking — run via asyncio.to_thread)."""
    with _http_client() as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        progress(done * 100.0 / total)


def _make_executable(path: Path) -> None:
    if not IS_WIN:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_member(archive: Path, member_name: str, dest: Path) -> None:
    """Pull one file out of a .zip or .tar.* archive by basename, writing it
    to dest. Raises FileNotFoundError if no member matches."""
    lowered = member_name.lower()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                base = Path(info.filename).name.lower()
                if base == lowered and not info.is_dir():
                    with zf.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    return
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for info in tf.getmembers():
                if info.isfile() and Path(info.name).name.lower() == lowered:
                    src = tf.extractfile(info)
                    if src is None:
                        continue
                    with src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    return
    raise FileNotFoundError(f"{member_name} not found inside {archive.name}")


async def _run_ok(cmd: list[str], timeout: float = 20.0) -> bool:
    """Does the binary actually run on this machine? Strict rc==0 — this is
    the arch/OS-compatibility gate for freshly downloaded binaries. exec of a
    wrong-architecture binary raises OSError rather than returning a code."""
    try:
        rc, _out, _err = await tools._run(cmd, timeout=timeout)
    except OSError:
        return False
    return rc == 0


def _atomic_install(tmp: Path, final: Path) -> None:
    _make_executable(tmp)
    os.replace(tmp, final)


# ---------------------------------------------------------------------------
# Per-tool installers
# ---------------------------------------------------------------------------

async def _install_ytdlp() -> str:
    dest = managed_tools_dir() / ("yt-dlp.exe" if IS_WIN else "yt-dlp")
    _step("ytdlp", "running", "下载 yt-dlp / downloading yt-dlp", 0)
    # Reuse the hardened downloader from tools.py: temp file + size/magic
    # verification + a real --version run before the atomic swap.
    await tools._download_latest_ytdlp(dest)
    config.update({"ytdlp_path": str(dest)})
    _step("ytdlp", "done", str(dest), 100)
    return str(dest)


async def _resolve_m3u8dl_asset_url() -> str:
    """Find the download URL of the latest N_m3u8DL-RE release asset for this
    platform without touching the rate-limited REST API. Falls back to the
    API if the HTML route breaks."""
    key = _m3u8dl_platform_key()

    def _via_html() -> Optional[str]:
        with _http_client() as client:
            # /releases/latest 302s to /releases/tag/<tag>
            r = client.get(f"https://github.com/{_M3U8DL_REPO}/releases/latest")
            r.raise_for_status()
            tag = str(r.url).rstrip("/").rsplit("/", 1)[-1]
            if not tag or tag == "latest":
                return None
            page = client.get(
                f"https://github.com/{_M3U8DL_REPO}/releases/expanded_assets/{tag}"
            )
            page.raise_for_status()
            hrefs = re.findall(
                r'href="([^"]*?/releases/download/[^"]+)"', page.text
            )
            for h in hrefs:
                if key in h and (h.endswith(".zip") or h.endswith(".tar.gz")):
                    return h if h.startswith("http") else f"https://github.com{h}"
        return None

    def _via_api() -> Optional[str]:
        with _http_client() as client:
            r = client.get(
                f"https://api.github.com/repos/{_M3U8DL_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            r.raise_for_status()
            for asset in r.json().get("assets", []):
                name = asset.get("name", "")
                if key in name and (name.endswith(".zip") or name.endswith(".tar.gz")):
                    return asset.get("browser_download_url")
        return None

    url = await asyncio.to_thread(_via_html)
    if not url:
        url = await asyncio.to_thread(_via_api)
    if not url:
        raise RuntimeError(
            f"could not find an N_m3u8DL-RE release asset for {key}"
        )
    return url


async def _install_m3u8dl() -> str:
    binname = "N_m3u8DL-RE.exe" if IS_WIN else "N_m3u8DL-RE"
    dest = managed_tools_dir() / binname
    _step("m3u8dl", "running", "查找 N_m3u8DL-RE 最新版 / locating latest release", 0)
    url = await _resolve_m3u8dl_asset_url()
    suffix = ".zip" if url.endswith(".zip") else ".tar.gz"
    # ignore_cleanup_errors: on Windows, antivirus (or the --help run we just
    # did) can briefly hold the extracted temp file, and a cleanup
    # PermissionError must not turn an already-completed install into a
    # reported failure.
    with tempfile.TemporaryDirectory(
        dir=managed_tools_dir(), ignore_cleanup_errors=True
    ) as td:
        archive = Path(td) / f"m3u8dl{suffix}"
        _step("m3u8dl", "running", "下载 N_m3u8DL-RE / downloading", 0)
        await asyncio.to_thread(
            _download_to, url, archive,
            lambda p: _step("m3u8dl", "running", "下载 N_m3u8DL-RE / downloading", p),
        )
        tmp = Path(td) / binname
        await asyncio.to_thread(_extract_member, archive, binname, tmp)
        _make_executable(tmp)
        # No --version flag; --help prints a header with the version and
        # exits 0. Any successful run proves the binary matches the CPU/OS.
        if not await _run_ok([str(tmp), "--help"]):
            raise RuntimeError("downloaded N_m3u8DL-RE failed to run")
        os.replace(tmp, dest)
    config.update({"m3u8dl_path": str(dest)})
    _step("m3u8dl", "done", str(dest), 100)
    return str(dest)


async def _install_ffmpeg() -> str:
    binname = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    dest = managed_tools_dir() / binname
    _step("ffmpeg", "running", "下载 ffmpeg / downloading ffmpeg", 0)

    if IS_WIN:
        urls = [_FFMPEG_WIN_URL]
    elif IS_MAC:
        urls = [_FFMPEG_MAC_URLS[_machine_arch()], _FFMPEG_MAC_FALLBACK]
    else:
        raise RuntimeError(
            "请用系统包管理器安装 ffmpeg（例如 sudo apt install ffmpeg）/ "
            "install ffmpeg via your package manager"
        )

    last_err: Optional[Exception] = None
    for url in urls:
        try:
            # ignore_cleanup_errors: see _install_m3u8dl.
            with tempfile.TemporaryDirectory(
                dir=managed_tools_dir(), ignore_cleanup_errors=True
            ) as td:
                archive = Path(td) / "ffmpeg.zip"
                await asyncio.to_thread(
                    _download_to, url, archive,
                    lambda p: _step("ffmpeg", "running", "下载 ffmpeg / downloading", p),
                )
                tmp = Path(td) / binname
                await asyncio.to_thread(_extract_member, archive, binname, tmp)
                _make_executable(tmp)
                if not await _run_ok([str(tmp), "-version"]):
                    raise RuntimeError("downloaded ffmpeg failed to run")
                os.replace(tmp, dest)
            config.update({"ffmpeg_path": str(dest)})
            _step("ffmpeg", "done", str(dest), 100)
            return str(dest)
        except Exception as e:  # noqa: BLE001 — try the next mirror
            last_err = e
    raise RuntimeError(f"ffmpeg download failed: {last_err}")


async def _install_js_runtime() -> str:
    """Install deno into the managed tools dir.

    Only reached when no runtime is already resolvable, so a machine that
    already has deno/node/bun never pays the ~40MB download.
    """
    binname = "deno.exe" if IS_WIN else "deno"
    dest = managed_tools_dir() / binname
    _step("jsruntime", "running", "下载 deno（YouTube 需要）/ downloading deno", 0)
    url = f"https://github.com/denoland/deno/releases/latest/download/{_deno_asset()}"
    # ignore_cleanup_errors: see _install_m3u8dl.
    with tempfile.TemporaryDirectory(
        dir=managed_tools_dir(), ignore_cleanup_errors=True
    ) as td:
        archive = Path(td) / "deno.zip"
        await asyncio.to_thread(
            _download_to, url, archive,
            lambda p: _step("jsruntime", "running", "下载 deno / downloading deno", p),
        )
        tmp = Path(td) / binname
        await asyncio.to_thread(_extract_member, archive, binname, tmp)
        _make_executable(tmp)
        if not await _run_ok([str(tmp), "--version"]):
            raise RuntimeError("downloaded deno failed to run")
        os.replace(tmp, dest)
    config.update({"js_runtime": str(dest)})
    _step("jsruntime", "done", str(dest), 100)
    return str(dest)


_INSTALLERS = {
    "ytdlp": _install_ytdlp,
    "m3u8dl": _install_m3u8dl,
    "ffmpeg": _install_ffmpeg,
    "jsruntime": _install_js_runtime,
}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def run_bootstrap() -> dict[str, Any]:
    """Download every missing tool. Safe to call repeatedly — a second call
    while one is running just returns the live status."""
    if _lock.locked():
        return get_status()
    async with _lock:
        todo = missing_tools()
        if not todo:
            _status["state"] = "done"
            return get_status()
        _status["state"] = "running"
        _status["error"] = None
        errors: list[str] = []
        for tool in todo:
            try:
                await _INSTALLERS[tool]()
            except Exception as e:  # noqa: BLE001 — keep going; partial success is useful
                msg = f"{tool}: {e}"
                errors.append(msg)
                _step(tool, "error", str(e)[:300])
        if errors:
            _status["state"] = "error"
            _status["error"] = " ; ".join(errors)[:1000]
        else:
            _status["state"] = "done"
        return get_status()


def start_background_bootstrap() -> None:
    """Fire-and-forget bootstrap at server startup, if enabled and needed.
    Must be called from inside a running event loop (FastAPI startup)."""
    try:
        enabled = bool(config.auto_download_tools)
    except AttributeError:
        enabled = True
    if not enabled:
        return
    if not missing_tools():
        return
    task = asyncio.create_task(run_bootstrap())
    # Keep a reference so the task isn't garbage-collected mid-download.
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


_background_tasks: set[asyncio.Task] = set()
