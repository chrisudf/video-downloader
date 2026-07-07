from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from .. import tools
from ..config import config
from ..models import DownloadRequest, FormatOption, InspectResult
from .base import BaseDownloader, ProgressCallback, ProgressEvent
from .registry import register

# We shell out to the user's yt-dlp.exe for BOTH probe and download. Rationale:
# YouTube's anti-bot machinery (n-challenge, PO tokens, "confirm you're not a
# bot") is a moving target the yt-dlp team patches constantly. A standalone
# yt-dlp.exe binary ships with a bundled JS runtime and updates as one blob;
# the pip package requires an external JS runtime (deno) and often breaks
# between releases. Using the exe for probe too means both paths share the
# same reliability characteristics.
#
# The pip-installed `yt_dlp` module is imported lazily only as a last-resort
# fallback for environments that have no exe on disk.


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


async def _probe_via_exe(url: str) -> dict[str, Any]:
    """Ask yt-dlp.exe for metadata as JSON. Preferred path — the bundled
    JS runtime handles YouTube's current n-challenge / bot check better
    than the pip package."""
    ytdlp = config.ytdlp_path
    # shutil.which handles both absolute paths and bare command names that
    # resolve via PATH. Path().exists() would falsely reject a bare "yt-dlp"
    # even when the exe is available on PATH.
    if shutil.which(ytdlp) is None:
        raise FileNotFoundError(ytdlp)
    args = [
        ytdlp, url,
        "--dump-single-json", "--no-download", "--no-warnings", "--no-playlist",
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not stdout:
        err = stderr.decode("utf-8", errors="replace").strip() or "yt-dlp.exe returned no output"
        # Strip ANSI colour codes yt-dlp emits on some terminals
        err = re.sub(r"\x1b\[[0-9;]*m", "", err)
        raise RuntimeError(err[-400:])
    return json.loads(stdout.decode("utf-8", errors="replace"))


async def _probe_via_pip(url: str) -> dict[str, Any]:
    """Last-resort fallback for environments without yt-dlp.exe on disk."""
    import yt_dlp  # noqa: PLC0415 — imported lazily; may not be installed
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
    }
    ff = tools.resolve_ffmpeg()
    if ff:
        opts["ffmpeg_location"] = ff

    def _run() -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)  # type: ignore[no-any-return]

    return await asyncio.to_thread(_run)


@register
class YouTubeDownloader(BaseDownloader):
    name = "youtube"

    @classmethod
    def can_handle(cls, url: str, *, content_hint: Optional[str] = None) -> bool:
        return bool(_YT_HOST_RE.match(url))

    async def probe(self, url: str, *, referer: Optional[str] = None) -> InspectResult:
        # Prefer the exe. Only fall back to the pip package when the exe is
        # genuinely absent — never on YouTube errors, since the pip package
        # tends to be strictly less capable than the exe on those.
        try:
            info = await _probe_via_exe(url)
        except FileNotFoundError:
            info = await _probe_via_pip(url)
        except Exception as e:  # noqa: BLE001
            # Append a "your yt-dlp is stale, hit Update in Settings" hint if
            # the exe is older than 30 days — most YouTube-side breakages are
            # fixed within days of yt-dlp release, so a stale exe is the #1
            # cause of these errors.
            info_v = await tools._ytdlp_info()
            hint = tools.stale_hint(info_v.get("age_days"))
            if hint:
                raise RuntimeError(f"{e}\n\n💡 {hint}")
            raise

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
        # Same check as probe: shutil.which accepts bare PATH-resolved names,
        # which Path().exists() would falsely reject — with the default
        # config ("yt-dlp" on PATH) probe succeeded but download refused.
        if shutil.which(ytdlp) is None:
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
        # --ffmpeg-location accepts either the binary or its directory. Resolve
        # via PATH — deriving .parent from a bare "ffmpeg" yields "." and
        # makes yt-dlp look for ffmpeg in the CWD only, breaking merges.
        ff = tools.resolve_ffmpeg()
        if ff:
            args += ["--ffmpeg-location", ff]

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
            # Stale yt-dlp is the most common root cause of download failures too.
            info_v = await tools._ytdlp_info()
            hint = tools.stale_hint(info_v.get("age_days"))
            msg = f"exit {rc}: {tail}"
            if hint:
                msg += f"\n\n💡 {hint}"
            await on_progress(ProgressEvent(status="error", message=msg))
            raise RuntimeError(f"yt-dlp exited {rc}: {tail}" + (f"\n\n💡 {hint}" if hint else ""))

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
