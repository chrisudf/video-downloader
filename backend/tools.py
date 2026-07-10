"""Metadata + self-update helpers for the external tools we shell out to
(yt-dlp.exe, N_m3u8DL-RE.exe, ffmpeg)."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .config import config


# yt-dlp uses YYYY.MM.DD version strings (e.g. "2026.03.17").
_YTDLP_VERSION_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})")

# N_m3u8DL-RE releases print "N_m3u8DL-RE (Beta version) YYYYMMDD" on --help.
_M3U8DL_VERSION_RE = re.compile(r"N_m3u8DL-RE.*?(\d{8})")

# ffmpeg prints "ffmpeg version 7.1.1" or similar.
_FFMPEG_VERSION_RE = re.compile(r"ffmpeg version (\S+)")


async def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    """Run a subprocess, capture stdout+stderr, tolerate timeouts."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return -1, "", "file not found"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        # Await the actual termination — otherwise repeated timeouts can
        # accumulate zombie processes and leave pipes un-drained.
        try:
            await proc.wait()
        except Exception:
            pass
        return -1, "", "timeout"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _age_days_from_ymd(y: int, m: int, d: int) -> Optional[int]:
    try:
        return (date.today() - date(y, m, d)).days
    except ValueError:
        return None


async def _ytdlp_info() -> dict[str, Any]:
    import shutil
    path = config.ytdlp_path
    # shutil.which resolves both absolute paths and PATH-based bare names,
    # returning None if neither actually exists.
    if shutil.which(path) is None:
        return {"path": path, "available": False}
    rc, out, err = await _run([path, "--version"], timeout=8.0)
    version = out.strip().splitlines()[0].strip() if out.strip() else None
    age = None
    if version:
        m = _YTDLP_VERSION_RE.match(version)
        if m:
            age = _age_days_from_ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return {
        "path": path,
        "available": rc == 0,
        "version": version,
        "age_days": age,
        "supports_self_update": True,
    }


async def _m3u8dl_info() -> dict[str, Any]:
    import shutil
    path = config.m3u8dl_path
    if shutil.which(path) is None:
        return {"path": path, "available": False}
    # N_m3u8DL-RE has no `--version`; the header line on --help contains it.
    rc, out, err = await _run([path, "--help"], timeout=8.0)
    combined = (out + err)[:400]
    m = _M3U8DL_VERSION_RE.search(combined)
    version = m.group(1) if m else None
    age = None
    if version and len(version) == 8:
        try:
            age = _age_days_from_ymd(int(version[:4]), int(version[4:6]), int(version[6:8]))
        except ValueError:
            age = None
    return {
        "path": path,
        "available": rc == 0 or bool(version),
        "version": version,
        "age_days": age,
        "supports_self_update": False,
    }


def resolve_ffmpeg() -> Optional[str]:
    """Absolute path to the ffmpeg binary, or None if it can't be found.
    Accepts both explicit paths and bare command names resolved via PATH.
    Callers must not derive a directory from the raw config value —
    Path("ffmpeg").parent is "." which always exists, silently pointing
    downstream tools at the current working directory instead of ffmpeg."""
    import shutil
    path = config.ffmpeg_path
    # config.update() doesn't validate types, so /api/config can store None
    # (or any junk) here — treat that as "not found" rather than letting
    # shutil.which() raise TypeError.
    if not isinstance(path, str) or not path:
        return None
    return shutil.which(path)


async def _ffmpeg_info() -> dict[str, Any]:
    import shutil
    path = config.ffmpeg_path
    if shutil.which(path) is None:
        return {"path": path, "available": False}
    rc, out, err = await _run([path, "-version"], timeout=8.0)
    combined = out + err
    m = _FFMPEG_VERSION_RE.search(combined)
    return {
        "path": path,
        "available": rc == 0,
        "version": m.group(1) if m else None,
        "supports_self_update": False,
    }


async def all_versions() -> dict[str, dict[str, Any]]:
    ytdlp, m3u8dl, ffmpeg = await asyncio.gather(
        _ytdlp_info(),
        _m3u8dl_info(),
        _ffmpeg_info(),
    )
    return {"ytdlp": ytdlp, "m3u8dl": m3u8dl, "ffmpeg": ffmpeg}


_PERMISSION_ERROR_RE = re.compile(
    r"Unable to write|try running as administrator|permission denied|Access is denied",
    re.IGNORECASE,
)

# `-U` asks api.github.com for the latest version before downloading anything;
# anonymous API calls are capped at 60/hour per IP, so shared IPs / VPNs hit
# "HTTP Error 403: rate limit exceeded" long before any real abuse.
_RATE_LIMIT_RE = re.compile(r"HTTP Error 403|rate.?limit", re.IGNORECASE)

# Release *download* URLs are served by GitHub's CDN and are not subject to
# the API rate limit, so they work even when the version check above 403s.
_YTDLP_RELEASE_ASSETS = {
    "win32": "yt-dlp.exe",
    "darwin": "yt-dlp_macos",
}


async def _download_latest_ytdlp(dest: Path) -> None:
    """Fetch the latest yt-dlp release binary straight from the CDN download
    URL (no api.github.com involved) and atomically replace `dest`.
    Runs in a worker thread so the ~20MB of network reads + disk writes
    never block the event loop (which may be pushing progress WebSockets)."""
    import os
    import sys as _sys
    import uuid

    import httpx

    asset = _YTDLP_RELEASE_ASSETS.get(_sys.platform, "yt-dlp")
    url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"
    # Unique temp name per attempt: concurrent updates must not interleave
    # writes into one file. Same directory as dest keeps os.replace atomic.
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.new")

    def _fetch() -> None:
        # Sweep temp files a previous hard-killed run may have left behind
        # (normal failures clean up after themselves below). Age-gated so a
        # concurrent update's in-progress temp file is never touched.
        import time
        for stale in dest.parent.glob(f"{dest.name}.*.new"):
            try:
                if time.time() - stale.stat().st_mtime > 3600:
                    stale.unlink()
            except OSError:
                pass
        try:
            with httpx.Client(
                follow_redirects=True, timeout=httpx.Timeout(180.0, connect=15.0)
            ) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_bytes(1 << 16):
                            fh.write(chunk)
            if os.name == "posix":
                os.chmod(tmp, 0o755)
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    await asyncio.to_thread(_fetch)


async def update_ytdlp() -> dict[str, Any]:
    """Run `yt-dlp.exe -U`. Returns combined stdout/stderr and new version.
    If the version check is rate-limited by the GitHub API, falls back to
    downloading the latest release binary directly."""
    import shutil
    path = config.ytdlp_path
    # Accept both absolute paths and bare command names (PATH lookup) —
    # consistent with _ytdlp_info above.
    resolved = shutil.which(path)
    if resolved is None:
        return {"ok": False, "error": f"yt-dlp not found at {path}", "log": ""}
    rc, out, err = await _run([path, "-U", "--no-colors"], timeout=60.0)
    log = (out + err).strip()
    log = re.sub(r"\x1b\[[0-9;]*m", "", log)  # strip ANSI just in case
    if rc != 0 and _RATE_LIMIT_RE.search(log):
        try:
            await _download_latest_ytdlp(Path(resolved))
            rc = 0
            log += "\n[fallback] GitHub API rate-limited; downloaded the latest release binary directly instead."
        except Exception as e:  # noqa: BLE001
            log += f"\n[fallback] direct download also failed: {e}"
    permission_error = rc != 0 and bool(_PERMISSION_ERROR_RE.search(log))
    # Re-query version after update
    new_info = await _ytdlp_info()
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "log": log[-4000:],
        "version_after": new_info.get("version"),
        "age_days_after": new_info.get("age_days"),
        "permission_error": permission_error,
        "suggested_relocation": _suggest_relocation_dest() if permission_error else None,
    }


def _suggest_relocation_dest() -> str:
    """Where to move yt-dlp.exe so future -U doesn't need admin.
    Windows: %LOCALAPPDATA%\\Programs\\yt-dlp\\yt-dlp.exe
    macOS/Linux: ~/.local/bin/yt-dlp
    """
    import os
    import sys as _sys

    if _sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(base) / "Programs" / "yt-dlp" / "yt-dlp.exe")
    return str(Path.home() / ".local" / "bin" / "yt-dlp")


async def relocate_ytdlp(destination: Optional[str] = None) -> dict[str, Any]:
    """Copy yt-dlp.exe to a user-writable location and point config there.
    Leaves the original file in place (deleting from a system dir would need
    the same admin rights we're trying to avoid)."""
    import shutil

    src = config.ytdlp_path
    if not Path(src).is_file():
        return {"ok": False, "error": f"source not found: {src}"}
    dest = destination or _suggest_relocation_dest()
    dest_path = Path(dest)
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)
    except PermissionError as e:
        return {"ok": False, "error": f"cannot write to {dest}: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    # Update config
    config.update({"ytdlp_path": str(dest_path)})
    import sys as _sys
    note = "Original file left in place. Delete manually if you want."
    if _sys.platform != "win32":
        # ~/.local/bin is often not on PATH (especially on macOS); that's
        # fine for us — config.json stores the absolute path — but worth
        # saying so the user isn't surprised when `yt-dlp` stops resolving
        # in their shell.
        note += (
            " Note: ~/.local/bin may not be on your PATH; the app is "
            "unaffected (it uses the absolute path from config.json)."
        )
    return {
        "ok": True,
        "source": src,
        "destination": str(dest_path),
        "note": note,
    }


def stale_hint(age_days: Optional[int], threshold: int = 30) -> Optional[str]:
    """Return a one-line hint if yt-dlp is older than `threshold` days."""
    if age_days is None:
        return None
    if age_days <= threshold:
        return None
    return (
        f"Your yt-dlp is {age_days} days old — YouTube ships anti-bot changes "
        f"faster than that. Open Settings ⚙ → click 'Update yt-dlp'."
    )
