from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from .. import tools
from ..config import config
from ..models import DownloadRequest, FormatOption, InspectResult
from .base import BaseDownloader, ProgressCallback, ProgressEvent
from .registry import register


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Regex helpers
_STREAM_INF_RE = re.compile(r"#EXT-X-STREAM-INF:([^\n]+)\n([^\n#]+)")
_M3U8_IN_HTML_RE = re.compile(r"""(?P<u>https?://[^\s'"<>]+\.m3u8[^\s'"<>]*)""", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(\d+\.\d+)\s*%")
_HTTP_STATUS_RE = re.compile(r"\b(\d{3})\b\s*\(([^)]+)\)")


def _summarize_failure(log_tail: list[str]) -> str:
    """Pull a human-readable error out of N_m3u8DL-RE's output, ignoring
    the .NET stack trace noise."""
    warn_lines: list[str] = []
    error_lines: list[str] = []
    for line in log_tail:
        # Skip .NET stack frames — after strip() they start with "at " or "---"
        if line.startswith("at ") or line.startswith("--- ") or line.startswith("System."):
            continue
        # Strip ANSI/timestamp prefix
        clean = re.sub(r"^\d{2}:\d{2}:\d{2}\.\d+\s*", "", line).strip()
        if re.search(r"\b(WARN|ERROR|Error|Unhandled exception|fail)\b", clean, re.IGNORECASE):
            if "Exception" in clean or "ERROR" in clean.upper():
                error_lines.append(clean)
            else:
                warn_lines.append(clean)

    joined = " | ".join(error_lines + warn_lines).lower()
    is_segment_failure = "segment" in joined or "first segment" in joined

    # Try to extract a specific HTTP status — most actionable
    for line in error_lines + warn_lines:
        m = _HTTP_STATUS_RE.search(line)
        if m:
            code, text = m.groups()
            if code.startswith("4") and is_segment_failure:
                return (
                    f"HTTP {code} {text.strip()} on stream segments — the m3u8 token most likely "
                    f"expired or is IP-bound to a different network. Re-fetch the URL from the source "
                    f"page (e.g. via 'Browser sniff') and start the download within the validity window."
                )
            if code.startswith("4"):
                return f"HTTP {code} {text.strip()} — auth/header issue. Try setting Referer to the source page."
            if code.startswith("5"):
                return f"HTTP {code} {text.strip()} — CDN server error. Try again later."

    if is_segment_failure:
        return ("Failed to download stream segments — the m3u8 URL probably expired or is "
                "IP-bound. Re-sniff and retry within the validity window.")

    # Fallback: first meaningful line
    for line in error_lines + warn_lines:
        if "Exception" in line:
            return f"N_m3u8DL-RE crashed: {line[:180]}"
        return line[:200]
    return "N_m3u8DL-RE exited with no recognizable error message"


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _parse_attr(attrs: str, key: str) -> Optional[str]:
    m = re.search(rf'{key}=("([^"]*)"|([\w/.-]+))', attrs)
    if not m:
        return None
    return m.group(2) or m.group(3)


def _build_headers(url: str, referer: Optional[str], extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": referer or _origin(url) + "/",
    }
    if extra:
        headers.update(extra)
    return headers


@register
class M3U8Downloader(BaseDownloader):
    name = "m3u8"

    @classmethod
    def can_handle(cls, url: str, *, content_hint: Optional[str] = None) -> bool:
        if ".m3u8" in url.lower():
            return True
        if content_hint and "mpegurl" in content_hint.lower():
            return True
        return False

    async def _fetch_m3u8_from_page(self, page_url: str, referer: Optional[str]) -> tuple[str, dict[str, str]]:
        """Scan an HTML page for an m3u8 URL. Returns (m3u8_url, headers)."""
        headers = _build_headers(page_url, referer or _origin(page_url))
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            r = await client.get(page_url, headers=headers)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if "mpegurl" in ct.lower() or page_url.lower().endswith(".m3u8"):
                return page_url, headers
            matches = list(_M3U8_IN_HTML_RE.finditer(r.text))
            if not matches:
                raise ValueError("No .m3u8 found in page. Paste the direct m3u8 URL.")
            # Prefer the first match that looks like a master playlist
            best = matches[0].group("u")
            return best, _build_headers(best, page_url)

    async def probe(self, url: str, *, referer: Optional[str] = None) -> InspectResult:
        if ".m3u8" in url.lower():
            m3u8_url = url
            headers = _build_headers(url, referer)
        else:
            m3u8_url, headers = await self._fetch_m3u8_from_page(url, referer)

        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            r = await client.get(m3u8_url, headers=headers)
            r.raise_for_status()
            playlist = r.text

        formats: list[FormatOption] = []
        variants = list(_STREAM_INF_RE.finditer(playlist))
        if variants:
            for m in variants:
                attrs = m.group(1)
                bw = _parse_attr(attrs, "BANDWIDTH")
                res = _parse_attr(attrs, "RESOLUTION") or ""
                codecs = _parse_attr(attrs, "CODECS")
                frame_rate = _parse_attr(attrs, "FRAME-RATE")
                height = int(res.split("x")[1]) if "x" in res else None
                label = f"{height}p" if height else (res or "stream")
                if bw:
                    label += f"  ({int(bw)//1000} kbps)"
                formats.append(
                    FormatOption(
                        id=f"res={res}" if res else f"bw={bw}",
                        label=label,
                        height=height,
                        fps=float(frame_rate) if frame_rate else None,
                        vcodec=codecs,
                        note=res or None,
                    )
                )
            # Always offer a "Best" choice
            formats.insert(0, FormatOption(id="best", label="Best (auto)"))
        else:
            # Not a master playlist — single-variant media playlist
            formats.append(FormatOption(id="best", label="Single stream"))

        # Derive a title from URL path if we don't have one
        title = Path(urlparse(m3u8_url).path).stem or "stream"

        return InspectResult(
            type="m3u8",
            source_url=url,
            resolved_url=m3u8_url,
            title=title,
            formats=formats,
            headers=headers,
            downloader=self.name,
        )

    async def download(
        self,
        request: DownloadRequest,
        on_progress: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> str:
        save_dir = Path(request.save_dir or config.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        m3u8_path = config.m3u8dl_path
        # shutil.which accepts bare PATH-resolved names too — Path().exists()
        # falsely rejects the default config value "N_m3u8DL-RE".
        if shutil.which(m3u8_path) is None:
            raise FileNotFoundError(f"N_m3u8DL-RE not found at {m3u8_path}. Check config.json")

        base = (request.filename_override or request.title or "video").strip()
        safe_title = re.sub(r"[\\/:*?\"<>|]", "_", base).strip() or "video"

        args: list[str] = [
            m3u8_path,
            request.url,
            "--save-name", safe_title,
            "--save-dir", str(save_dir),
            "--check-segments-count", "False",
        ]

        # Quality selector. Probe ids are "res=WxH" (variants carrying a
        # RESOLUTION attribute) or "bw=..." (variants without one).
        # N_m3u8DL-RE's filter keys are regex-valued (res=, codecs=, ...) and
        # bandwidth is not among them, so "bw=..." was never a valid filter.
        # Exact-match the resolution when we have one; otherwise fall back to
        # auto-selecting the best stream. --auto-select and an explicit
        # selection are mutually exclusive here so they can't disagree.
        fid = request.format_id or "best"
        if fid.startswith("res=") and fid != "res=":
            res = fid[len("res="):]
            args += [
                "--select-video", f"res=^{re.escape(res)}$:for=best",
                "--select-audio", "best",
            ]
        else:
            args += ["--auto-select"]

        # ffmpeg location (for muxing). Resolve via PATH — the raw config
        # value may be a bare "ffmpeg", whose .parent "." would be wrong.
        ff = tools.resolve_ffmpeg()
        if ff:
            args += ["--ffmpeg-binary-path", ff]

        # Headers — combine Referer/UA + any extra
        merged_headers = dict(request.headers)
        merged_headers.setdefault("User-Agent", DEFAULT_UA)
        for k, v in merged_headers.items():
            args += ["--header", f"{k}: {v}"]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        log_tail: list[str] = []
        last_pct = 0.0
        assert proc.stdout is not None

        async def cancel_watcher() -> None:
            await cancel_event.wait()
            if proc.returncode is None:
                proc.terminate()

        watcher_task = asyncio.create_task(cancel_watcher())

        try:
            buffer = bytearray()
            while True:
                chunk = await proc.stdout.read(256)
                if not chunk:
                    break
                buffer.extend(chunk)
                # N_m3u8DL-RE uses both \n and \r for progress updates
                while True:
                    nl = -1
                    for i, b in enumerate(buffer):
                        if b in (0x0A, 0x0D):
                            nl = i
                            break
                    if nl == -1:
                        break
                    raw = bytes(buffer[:nl]).decode("utf-8", errors="replace").strip()
                    del buffer[:nl + 1]
                    if not raw:
                        continue
                    log_tail.append(raw)
                    if len(log_tail) > 50:
                        del log_tail[:-50]
                    # Suppress .NET stack frames from the live log shown in UI
                    if raw.startswith("at ") or raw.startswith("--- ") or raw.startswith("System."):
                        continue
                    m = _PERCENT_RE.search(raw)
                    if m:
                        pct = float(m.group(1))
                        if pct >= last_pct or pct == 0.0:
                            last_pct = pct
                            await on_progress(
                                ProgressEvent(
                                    percent=pct,
                                    status="running",
                                    message=raw[-160:],
                                )
                            )
                    else:
                        await on_progress(
                            ProgressEvent(
                                percent=last_pct,
                                status="running",
                                message=raw[-160:],
                            )
                        )

            rc = await proc.wait()
        finally:
            watcher_task.cancel()

        if cancel_event.is_set():
            await on_progress(ProgressEvent(status="error", message="cancelled"))
            raise asyncio.CancelledError("cancelled")
        if rc != 0:
            reason = _summarize_failure(log_tail)
            await on_progress(ProgressEvent(status="error", message=reason))
            raise RuntimeError(reason)

        # Find the produced file — N_m3u8DL-RE writes <save-name>.<ext>
        candidates = sorted(save_dir.glob(f"{safe_title}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = str(candidates[0]) if candidates else ""
        await on_progress(ProgressEvent(percent=100.0, status="done", output_file=out))
        return out
