from __future__ import annotations

import asyncio
import re
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import config
from ..models import DownloadRequest, FormatOption, InspectResult
from .base import BaseDownloader, ProgressCallback, ProgressEvent
from .registry import register

# yt-dlp Python package — used for probing (fast metadata extraction).
# For the actual download we shell out to the user's yt-dlp.exe, which is
# more robust against YouTube's changing anti-bot machinery and tracks the
# exe's own update cadence (the pip package often needs PO tokens).
import yt_dlp


_YT_HOST_RE = re.compile(
    r"^(https?://)?([\w-]+\.)*(youtube\.com|youtu\.be|bilibili\.com|vimeo\.com|"
    r"twitch\.tv|nicovideo\.jp|dailymotion\.com|twitter\.com|x\.com|tiktok\.com|"
    r"instagram\.com|facebook\.com|reddit\.com|soundcloud\.com)/",
    re.IGNORECASE,
)

# yt-dlp progress line: "[download]  16.1% of   12.43MiB at    2.44MiB/s ETA 00:04"
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<pct>\d+\.\d+)%\s+of\s+~?\s*\S+\s+at\s+(?P<speed>\S+\s*\S*)\s+ETA\s+(?P<eta>[\d:\-]+)"
)
_DEST_RE = re.compile(r"\[(?:download|Merger|ExtractAudio)\]\s+(?:Destination|Merging formats into):?\s+\"?(?P<path>.+?)\"?$")


def _make_probe_opts(cookies_browser: Optional[str] = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
    }
    ff_dir = Path(config.ffmpeg_path).parent
    if ff_dir.exists():
        opts["ffmpeg_location"] = str(ff_dir)
    # A manually exported cookies file is the most reliable option on modern
    # Windows Chrome (App-Bound Encryption bypasses --cookies-from-browser).
    cookies_file = getattr(config, "youtube_cookies_file", "") or ""
    if cookies_file and Path(cookies_file).is_file():
        opts["cookiefile"] = cookies_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


_BOT_CHECK_RE = re.compile(r"Sign in to confirm|not a bot|cookies for the authentication", re.IGNORECASE)

# Remember which cookie-browser worked most recently so the download path uses
# the same one instead of re-walking the fallback chain (and possibly wasting a
# partial download attempt on a broken browser).
_LAST_WORKING_BROWSER: Optional[str] = None
_COOKIE_UNAVAILABLE_RE = re.compile(
    r"Could not copy .* cookie database|Failed to load cookies|does not exist|"
    r"Failed to decrypt with DPAPI|no supported browsers found|"
    r"could not find .* (cookie|profile|database|installation)",
    re.IGNORECASE,
)


def _cookie_browser_chain() -> list[str]:
    """The browser to try first (from config), then a fallback list."""
    primary = getattr(config, "youtube_cookies_from", "") or ""
    fallbacks = ["chrome", "edge", "brave", "firefox", "chromium", "opera"]
    if sys.platform == "darwin":
        fallbacks.append("safari")
    chain: list[str] = []
    if primary:
        chain.append(primary)
    for b in fallbacks:
        if b not in chain:
            chain.append(b)
    return chain


@register
class YouTubeDownloader(BaseDownloader):
    name = "youtube"

    @classmethod
    def can_handle(cls, url: str, *, content_hint: Optional[str] = None) -> bool:
        return bool(_YT_HOST_RE.match(url))

    async def probe(self, url: str, *, referer: Optional[str] = None) -> InspectResult:
        def _run(browser: Optional[str]) -> dict[str, Any]:
            with yt_dlp.YoutubeDL(_make_probe_opts(cookies_browser=browser)) as ydl:
                return ydl.extract_info(url, download=False)  # type: ignore[no-any-return]

        info: Optional[dict[str, Any]] = None
        # 1. Try without cookies first — most public videos still work
        try:
            info = await asyncio.to_thread(_run, None)
        except yt_dlp.utils.DownloadError as e:
            if not _BOT_CHECK_RE.search(str(e)):
                raise
            # 2. Fall back to browser cookies, walking the config → chrome → edge → …
            #    chain until one succeeds. Skip any that hit a "cookie DB locked"
            #    error (browser is running with exclusive lock).
            tried: list[str] = []
            last_err: Optional[Exception] = e
            global _LAST_WORKING_BROWSER
            for browser in _cookie_browser_chain():
                tried.append(browser)
                try:
                    info = await asyncio.to_thread(_run, browser)
                    _LAST_WORKING_BROWSER = browser
                    break
                except yt_dlp.utils.DownloadError as sub:
                    last_err = sub
                    if _COOKIE_UNAVAILABLE_RE.search(str(sub)):
                        continue  # this browser can't be read — try next
                    if _BOT_CHECK_RE.search(str(sub)):
                        continue  # cookies were readable but didn't help
                    raise
            if info is None:
                raise RuntimeError(
                    "YouTube requires cookies for this video and no installed browser worked "
                    f"(tried: {', '.join(tried)}). On modern Windows Chrome (127+) the cookie "
                    "database is protected by app-bound encryption that yt-dlp cannot decrypt. "
                    "Pick one of these fixes:\n"
                    " 1. Install Firefox → Settings ⚙ → 'YouTube cookies from' → Firefox (its "
                    "cookies aren't affected).\n"
                    " 2. Export cookies to a file:\n"
                    "    a. Install a 'Get cookies.txt LOCALLY' extension in Chrome (or any browser "
                    "where you're logged into YouTube).\n"
                    "    b. Visit youtube.com, click the extension, export cookies.txt.\n"
                    "    c. In Settings ⚙, paste the file path into 'YouTube cookies 文件'.\n"
                    " 3. Or close every Chrome window/process (including Task Manager background "
                    "instances) and retry — that unlocks the older non-DPAPI cookie DB.\n"
                    f"Last error: {str(last_err)[:200]}"
                )
        assert info is not None

        if info.get("_type") == "playlist" and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if entries:
                info = entries[0]

        raw_formats = info.get("formats") or []
        heights = sorted(
            {f["height"] for f in raw_formats if f.get("height") and f.get("vcodec") != "none"},
            reverse=True,
        )

        options: list[FormatOption] = [
            FormatOption(id="bv*+ba/b", label="Best (auto)", note="best video + best audio")
        ]
        for h in heights:
            options.append(
                FormatOption(
                    id=f"bv*[height<={h}]+ba/b[height<={h}]",
                    label=f"{h}p",
                    height=h,
                )
            )
        options.append(FormatOption(id="ba", label="Audio only (best)", note="m4a/opus"))

        return InspectResult(
            type="youtube",
            source_url=url,
            resolved_url=info.get("webpage_url") or url,
            title=info.get("title"),
            thumbnail=info.get("thumbnail"),
            duration=info.get("duration"),
            uploader=info.get("uploader") or info.get("channel"),
            formats=options,
            downloader=self.name,
            raw={"extractor": info.get("extractor"), "id": info.get("id")},
        )

    async def download(
        self,
        request: DownloadRequest,
        on_progress: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> str:
        save_dir = Path(request.save_dir or config.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)

        ytdlp = config.ytdlp_path
        if not Path(ytdlp).exists():
            raise FileNotFoundError(
                f"yt-dlp not found at {ytdlp}. Set 'ytdlp_path' in config.json."
            )

        # Explicit user-typed filename wins. Otherwise use yt-dlp's own template.
        custom = (request.filename_override or "").strip()
        if custom:
            safe = re.sub(r'[\\/:*?"<>|]', "_", custom)
            outtmpl = str(save_dir / f"{safe}.%(ext)s")
        else:
            outtmpl = str(save_dir / "%(title)s [%(id)s].%(ext)s")
        args = [
            ytdlp,
            request.url,
            "-f", request.format_id,
            "-o", outtmpl,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--newline",       # progress on its own line instead of \r updates
            "--no-color",
            "--concurrent-fragments", "4",
            "--retries", "10",
        ]
        ff_dir = Path(config.ffmpeg_path).parent
        if ff_dir.exists():
            args += ["--ffmpeg-location", str(ff_dir)]

        # Cookies for the YouTube bot check. Precedence:
        #   1. Manually exported cookies.txt (most reliable on modern Chrome)
        #   2. Browser that recently worked for probe (cached)
        #   3. Configured default browser
        cookies_file = getattr(config, "youtube_cookies_file", "") or ""
        if cookies_file and Path(cookies_file).is_file():
            args += ["--cookies", cookies_file]
        else:
            browser = _LAST_WORKING_BROWSER or getattr(config, "youtube_cookies_from", "")
            if browser:
                args += ["--cookies-from-browser", browser]

        # Pass through any user-supplied headers (cookies, custom Referer, ...)
        for k, v in request.headers.items():
            args += ["--add-header", f"{k}:{v}"]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None

        async def cancel_watcher() -> None:
            await cancel_event.wait()
            if proc.returncode is None:
                proc.terminate()

        watcher_task = asyncio.create_task(cancel_watcher())

        log_tail: list[str] = []
        last_pct = 0.0
        output_file: Optional[str] = None

        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                log_tail.append(raw)
                if len(log_tail) > 30:
                    del log_tail[:-30]

                m = _PROGRESS_RE.search(raw)
                if m:
                    pct = float(m.group("pct"))
                    if pct >= last_pct or pct < 1.0:
                        last_pct = pct
                        await on_progress(
                            ProgressEvent(
                                percent=pct,
                                speed=m.group("speed").strip(),
                                eta=m.group("eta"),
                                status="running",
                            )
                        )
                    continue

                dest = _DEST_RE.search(raw)
                if dest:
                    output_file = dest.group("path").strip().strip('"')

                # Anything else: surface as log line
                await on_progress(ProgressEvent(percent=last_pct, status="running", message=raw[-160:]))

            rc = await proc.wait()
        finally:
            watcher_task.cancel()

        if cancel_event.is_set():
            await on_progress(ProgressEvent(status="error", message="cancelled"))
            raise asyncio.CancelledError("cancelled")
        if rc != 0:
            tail = " | ".join(log_tail[-5:])
            await on_progress(ProgressEvent(status="error", message=f"exit {rc}: {tail}"))
            raise RuntimeError(f"yt-dlp exited {rc}: {tail}")

        # Best-effort: locate the produced file. The Merger line is most reliable.
        # Fall back to the most recently modified file in save_dir.
        if not output_file or not Path(output_file).exists():
            candidates = sorted(
                (p for p in save_dir.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                output_file = str(candidates[0])

        await on_progress(ProgressEvent(percent=100.0, status="done", output_file=output_file))
        return output_file or ""
