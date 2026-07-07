from __future__ import annotations

import asyncio
import webbrowser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import tools
from .browser_sniff import is_available as sniff_available, sniff
from .config import config
from .detector import pick_downloader, list_downloaders
from .downloads_manager import manager
from .models import DownloadRequest, InspectRequest, InspectResult, JobStatus

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(title="Video Downloader", version="0.1.0")


# ---- Local-only request guard ----------------------------------------
# This server executes user-configurable binaries (yt-dlp, N_m3u8DL-RE) and
# lets /api/config repoint those paths, so a web page must never be able to
# reach it cross-site. Reject any request whose Host header isn't loopback
# (DNS rebinding) or whose Origin is another site (CSRF).

_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _is_local(netloc_or_origin: str) -> bool:
    value = netloc_or_origin if "//" in netloc_or_origin else f"//{netloc_or_origin}"
    try:
        host = urlparse(value).hostname
    except ValueError:
        return False
    return host in _LOCAL_HOSTNAMES


@app.middleware("http")
async def _reject_cross_site(request: Request, call_next):  # type: ignore[no-untyped-def]
    if not _is_local(request.headers.get("host", "")):
        return JSONResponse({"detail": "forbidden Host header"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and not _is_local(origin):
        return JSONResponse({"detail": "cross-origin requests are not allowed"}, status_code=403)
    return await call_next(request)


# ---- API -------------------------------------------------------------

@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return config.as_dict()


@app.post("/api/config")
def update_config(patch: dict[str, Any]) -> dict[str, Any]:
    config.update(patch)
    return config.as_dict()


@app.get("/api/downloaders")
def get_downloaders() -> list[str]:
    return list_downloaders()


@app.get("/api/tools/versions")
async def tool_versions() -> dict[str, Any]:
    return await tools.all_versions()


@app.post("/api/tools/update_ytdlp")
async def update_ytdlp() -> dict[str, Any]:
    return await tools.update_ytdlp()


@app.post("/api/tools/relocate_ytdlp")
async def relocate_ytdlp(payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    dest = (payload or {}).get("destination")
    return await tools.relocate_ytdlp(destination=dest)


@app.post("/api/inspect", response_model=InspectResult)
async def inspect(req: InspectRequest) -> InspectResult:
    cls = pick_downloader(req.url)
    if cls:
        try:
            return await cls().probe(req.url, referer=req.referer)
        except Exception as e:  # noqa: BLE001
            # If the URL claimed to be m3u8/page but probe failed, try headless sniff as a last resort
            if cls.name != "m3u8":
                raise HTTPException(status_code=400, detail=f"Probe failed: {e}")
            # fall through to sniff

    # Unknown URL — try headless Playwright sniff as fallback
    if not sniff_available():
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not identify a downloader for this URL. "
                "Install Playwright for browser-based sniffing of arbitrary sites."
            ),
        )
    result = await sniff(req.url, mode="headless", referer=req.referer, timeout_seconds=18)
    if not result.media:
        raise HTTPException(
            status_code=400,
            detail=(
                "Auto-sniff found no media. This site likely needs manual interaction. "
                "Click 'Open browser to sniff' to play the video manually, "
                "or paste the m3u8 URL + Referer directly."
            ),
        )
    first = result.media[0]
    # Probe the discovered m3u8 via the m3u8 downloader
    from .downloaders.m3u8_dl import M3U8Downloader
    probe = await M3U8Downloader().probe(first.url, referer=first.referer or req.referer or req.url)
    probe.source_url = req.url
    return probe


class SniffRequest(InspectRequest):
    mode: str = "headed"   # "headed" | "headless"
    timeout_seconds: float = 60.0


@app.post("/api/sniff")
async def browser_sniff(req: SniffRequest) -> dict[str, Any]:
    """Open a Chromium window so the user can click play manually.
    Returns the captured media URLs. Pass one back via /api/inspect or
    directly to /api/download as an m3u8 source."""
    if not sniff_available():
        raise HTTPException(500, "Playwright is not installed")
    mode = "headed" if req.mode == "headed" else "headless"
    result = await sniff(req.url, mode=mode, referer=req.referer, timeout_seconds=req.timeout_seconds)
    return {
        "page_url": result.page_url,
        "media": [
            {"url": m.url, "referer": m.referer, "frame_url": m.frame_url, "headers": m.headers}
            for m in result.media
        ],
        "iframes": result.iframes,
        "error": result.error,
    }


@app.post("/api/download")
async def start_download(req: DownloadRequest) -> JobStatus:
    if req.downloader not in list_downloaders():
        raise HTTPException(400, f"Unknown downloader: {req.downloader}")
    job = manager.submit(req)
    return job.status


@app.get("/api/jobs", response_model=list[JobStatus])
def list_jobs() -> list[JobStatus]:
    return manager.list()


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job.status


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    ok = manager.cancel(job_id)
    if not ok:
        raise HTTPException(404, "no such job")
    return {"cancelled": True}


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str) -> dict[str, Any]:
    if not manager.remove(job_id):
        raise HTTPException(400, "job not found or still running (cancel first)")
    return {"removed": True}


@app.post("/api/jobs/clear_finished")
async def clear_finished() -> dict[str, Any]:
    n = manager.clear_finished()
    return {"removed": n}


@app.post("/api/jobs/{job_id}/reveal")
async def reveal_job(job_id: str) -> dict[str, Any]:
    """Open Windows Explorer with the job's output file selected."""
    import subprocess
    import sys

    job = manager.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    target = job.status.output_file or job.status.save_dir
    if not target or not Path(target).exists():
        raise HTTPException(404, f"path does not exist: {target}")
    target_path = str(Path(target).resolve())
    if sys.platform == "win32":
        # /select highlights the file inside the folder
        if Path(target_path).is_file():
            subprocess.Popen(["explorer", f"/select,{target_path}"])
        else:
            subprocess.Popen(["explorer", target_path])
    elif sys.platform == "darwin":
        # `open` treats an empty-string argument as a (missing) file, so the
        # -R flag must be present-or-absent, never "".
        if Path(target_path).is_file():
            subprocess.Popen(["open", "-R", target_path])
        else:
            subprocess.Popen(["open", target_path])
    else:
        subprocess.Popen(["xdg-open", str(Path(target_path).parent if Path(target_path).is_file() else target_path)])
    return {"opened": target_path}


@app.websocket("/ws/progress/{job_id}")
async def ws_progress(ws: WebSocket, job_id: str) -> None:
    # WebSockets bypass CORS entirely — apply the same origin policy as the
    # HTTP middleware above.
    origin = ws.headers.get("origin")
    if origin and not _is_local(origin):
        await ws.close(code=4403)
        return
    job = manager.get(job_id)
    if not job:
        await ws.close(code=4004)
        return
    await ws.accept()
    q = job.subscribe()
    try:
        while True:
            data = await q.get()
            await ws.send_json(data)
            if data.get("__closed__"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        job.unsubscribe(q)


# ---- Static frontend -------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def main() -> None:
    import sys

    import uvicorn

    # Playwright spawns its own driver subprocess. On Windows that needs the
    # Proactor event loop — uvicorn[standard] defaults to the selector loop,
    # which fails with "spawn UNKNOWN".
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    url = f"http://127.0.0.1:{config.port}"
    print(f"\nVideo Downloader running at {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="info", loop="asyncio")


if __name__ == "__main__":
    main()
