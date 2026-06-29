from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

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
        if not Path(m3u8_path).exists():
            raise FileNotFoundError(f"N_m3u8DL-RE not found at {m3u8_path}. Check config.json")

        safe_title = re.sub(r"[\\/:*?\"<>|]", "_", (request.title or "video")).strip() or "video"

        args: list[str] = [
            m3u8_path,
            request.url,
            "--save-name", safe_title,
            "--save-dir", str(save_dir),
            "--auto-select",
            "--check-segments-count", "False",
        ]

        # Apply quality selector
        fid = request.format_id
        if fid and fid != "best":
            if fid.startswith("res="):
                args += ["--select-video", fid]
            elif fid.startswith("bw="):
                args += ["--select-video", fid]
            else:
                args += ["--select-video", fid]

        # ffmpeg location (for muxing)
        ff_dir = Path(config.ffmpeg_path).parent
        if ff_dir.exists():
            args += ["--ffmpeg-binary-path", config.ffmpeg_path]

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
            tail = " | ".join(log_tail[-5:])
            await on_progress(ProgressEvent(status="error", message=f"exit {rc}: {tail}"))
            raise RuntimeError(f"N_m3u8DL-RE exited {rc}: {tail}")

        # Find the produced file — N_m3u8DL-RE writes <save-name>.<ext>
        candidates = sorted(save_dir.glob(f"{safe_title}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = str(candidates[0]) if candidates else ""
        await on_progress(ProgressEvent(percent=100.0, status="done", output_file=out))
        return out
