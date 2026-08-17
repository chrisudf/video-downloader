from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from .. import tools
from ..config import config, normalize_headers
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

# Query parameters CDNs use for time-limited signed URLs. Their presence means
# a 403 is far more likely to be an expired/IP-bound token than a missing
# header — and no amount of retrying or header tweaking will fix that.
#
# Kept deliberately narrow. Claiming "your signature expired" when the real
# problem is a missing Referer sends the user chasing the wrong thing, so
# generic names that merely *might* be signing params (e, st, key, id) are
# excluded — a missed detection just yields the neutral message, whereas a
# false positive actively misleads.
#
# Note this only sees query strings. A token embedded in the URL *path* is
# indistinguishable from an ordinary path segment and will not be detected.
_SIGNED_URL_PARAM_RE = re.compile(
    r"(?:^|[?&])(?:"
    r"expires?|exp|start_?time|end_?time|valid(?:from|to|until)?|"
    r"token|sig|signature|policy|credential|md5|"
    r"x-amz-(?:signature|expires|credential)|__gda__|_hdnea_"
    r")=",
    re.IGNORECASE,
)

# Response bodies servers return when a signed URL has lapsed.
_EXPIRY_BODY_RE = re.compile(
    r"expired|expire|signature|token|not\s+valid|invalid\s+(?:key|token|sig)|"
    r"access\s+denied|forbidden.*(?:time|date)",
    re.IGNORECASE,
)


def _is_signed_url(url: str) -> bool:
    return bool(_SIGNED_URL_PARAM_RE.search(url))


def _explain_block(status: int, url: str, body: str) -> str:
    """Turn a definitive HTTP rejection into an actionable message."""
    signed = _is_signed_url(url)
    body_hints_expiry = bool(_EXPIRY_BODY_RE.search(body[:600]))

    if status in (401, 403):
        if signed or body_hints_expiry:
            return (
                f"HTTP {status} — this URL carries a time-limited signature, so it has most "
                f"likely expired or is bound to the IP/session that generated it. Retrying "
                f"will not help and neither will custom headers: re-fetch the URL from the "
                f"source page and start the download promptly."
            )
        return (
            f"HTTP {status} — the server rejected the request. This is usually a missing "
            f"Referer, Cookie or User-Agent. Set them in Settings -> Custom headers (or the "
            f"Referer field for a one-off) and try again."
        )
    if status == 404:
        return "HTTP 404 — the playlist is no longer at this URL. Re-fetch it from the source page."
    if status == 410:
        return "HTTP 410 — the server says this playlist is permanently gone. Re-fetch it."
    return f"HTTP {status} — the server refused to serve the playlist."


async def _preflight(url: str, headers: dict[str, str]) -> Optional[str]:
    """Fetch the playlist once, with exactly the headers the downloader will
    use, to find out whether it is reachable at all.

    Returns an error message if the server *definitively* rejected us, else
    None. N_m3u8DL-RE retries a failing manifest 10 times with no way to turn
    that off, so without this a dead URL costs a minute of retries and then
    reports a vague segment error. Deliberately conservative: anything other
    than a clear client-side rejection (timeouts, DNS, TLS, 5xx) returns None
    so the real downloader still gets its chance."""
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (401, 403, 404, 410):
                    body = ""
                    try:
                        chunks = []
                        async for chunk in resp.aiter_bytes(2048):
                            chunks.append(chunk)
                            if sum(len(c) for c in chunks) >= 2048:
                                break
                        body = b"".join(chunks).decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001 — body is a nicety, not required
                        pass
                    return _explain_block(resp.status_code, str(resp.url), body)
    except Exception:  # noqa: BLE001
        # Network hiccup, odd TLS, a server that dislikes httpx specifically —
        # not grounds to refuse the download.
        return None
    return None


def _summarize_failure(log_tail: list[str], url: str = "") -> str:
    """Pull a human-readable error out of N_m3u8DL-RE's output, ignoring
    the .NET stack trace noise. `url` lets a 4xx be attributed to an expired
    signature only when the URL actually carries one."""
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
                if _is_signed_url(url):
                    return (
                        f"HTTP {code} {text.strip()} on stream segments — this URL carries a "
                        f"time-limited signature, which has expired or is bound to another "
                        f"IP/session. Re-fetch it from the source page and start the download "
                        f"promptly; retrying this URL will not help."
                    )
                # No signature in the URL, so blaming an expired token would
                # send the user chasing the wrong thing — the playlist was
                # reachable (we pre-flighted it) but segments were refused,
                # which usually means they need a header of their own.
                return (
                    f"HTTP {code} {text.strip()} on stream segments — the playlist loaded but the "
                    f"segments were refused. They may need a Referer/Cookie of their own "
                    f"(Settings -> Custom headers), or the stream is restricted by IP/region."
                )
            if code.startswith("4"):
                return (
                    f"HTTP {code} {text.strip()} — auth/header issue. Set Referer/Cookie in "
                    f"Settings -> Custom headers, or use the Referer field for a one-off."
                )
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
    """Precedence, weakest first: our built-in defaults, the headers
    configured in Settings, then `extra` (an explicit call-site override).

    `referer` is deliberately ranked ABOVE the configured Referer: it is the
    page we actually observed this URL on, whereas the configured one is a
    global default. Letting a global value shadow a correctly-derived
    per-site Referer would break the common flow to fix the rare one. The
    configured Referer still applies whenever we'd otherwise be guessing from
    the URL's own origin."""
    headers = {"User-Agent": DEFAULT_UA}
    headers.update(config.headers())
    headers["Referer"] = (
        referer or headers.get("Referer") or _origin(url) + "/"
    )
    if extra:
        headers.update(normalize_headers(extra))
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

        # Headers — configured defaults first, then whatever this specific
        # request carries (probe-derived Referer, or a Referer the user typed
        # into the direct-m3u8 form), which wins on conflict.
        # Normalise each side before merging: sniffed request headers arrive
        # lower-cased from the browser, so merging raw would keep "referer"
        # and "Referer" as two keys instead of letting the request win.
        normalized = {
            **normalize_headers(config.headers()),
            **normalize_headers(request.headers),
        }
        normalized.setdefault("User-Agent", DEFAULT_UA)
        for k, v in normalized.items():
            args += ["--header", f"{k}: {v}"]

        # Check the playlist ourselves before handing off. N_m3u8DL-RE retries
        # an unreachable manifest 10 times with no flag to disable it, so a
        # dead URL otherwise burns ~a minute and then reports a misleading
        # "failed to download segments".
        block = await _preflight(request.url, normalized)
        if block:
            await on_progress(ProgressEvent(status="error", message=block))
            raise RuntimeError(block)

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
            reason = _summarize_failure(log_tail, request.url)
            await on_progress(ProgressEvent(status="error", message=reason))
            raise RuntimeError(reason)

        # Find the produced file — N_m3u8DL-RE writes <save-name>.<ext>
        candidates = sorted(save_dir.glob(f"{safe_title}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = str(candidates[0]) if candidates else ""
        await on_progress(ProgressEvent(percent=100.0, status="done", output_file=out))
        return out
