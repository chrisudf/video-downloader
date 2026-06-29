from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator

from .config import config
from .downloaders.base import BaseDownloader, ProgressEvent
from .downloaders.registry import get_downloader
from .models import DownloadRequest, JobStatus


class DownloadJob:
    def __init__(self, request: DownloadRequest):
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.status = JobStatus(
            job_id=self.id,
            url=request.url,
            title=request.title,
            downloader=request.downloader,
            format_id=request.format_id,
            status="queued",
            save_dir=request.save_dir or config.save_dir,
            created_at=time.time(),
        )
        self.cancel_event = asyncio.Event()
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._task: asyncio.Task[Any] | None = None
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(q)
        # Replay current state so a late subscriber sees something
        q.put_nowait(self._snapshot())
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _snapshot(self) -> dict[str, Any]:
        return self.status.model_dump()

    async def _broadcast(self) -> None:
        snap = self._snapshot()
        for q in list(self._subscribers):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                pass

    async def _on_progress(self, evt: ProgressEvent) -> None:
        async with self._lock:
            if evt.percent:
                self.status.percent = max(self.status.percent, evt.percent)
            if evt.speed is not None:
                self.status.speed = evt.speed
            if evt.eta is not None:
                self.status.eta = evt.eta
            if evt.message:
                self.status.log_tail.append(evt.message)
                if len(self.status.log_tail) > 30:
                    self.status.log_tail = self.status.log_tail[-30:]
            if evt.output_file:
                self.status.output_file = evt.output_file
            if evt.status == "done":
                self.status.status = "done"
                self.status.percent = 100.0
                self.status.finished_at = time.time()
            elif evt.status == "error":
                self.status.status = "error"
                self.status.error = evt.message
                self.status.finished_at = time.time()
        await self._broadcast()

    async def run(self) -> None:
        self.status.status = "running"
        await self._broadcast()
        try:
            downloader: BaseDownloader = get_downloader(self.request.downloader)
            await downloader.download(self.request, self._on_progress, self.cancel_event)
        except asyncio.CancelledError:
            self.status.status = "cancelled"
            self.status.finished_at = time.time()
            await self._broadcast()
            raise
        except Exception as e:  # noqa: BLE001
            self.status.status = "error"
            self.status.error = str(e)
            self.status.finished_at = time.time()
            await self._broadcast()
        finally:
            # Sentinel so subscribers can close
            for q in list(self._subscribers):
                q.put_nowait({"__closed__": True, **self._snapshot()})

    def cancel(self) -> None:
        self.cancel_event.set()
        if self._task and not self._task.done():
            self._task.cancel()


class DownloadManager:
    def __init__(self, max_concurrent: int = 2):
        self.jobs: dict[str, DownloadJob] = {}
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._order: list[str] = []

    def submit(self, request: DownloadRequest) -> DownloadJob:
        job = DownloadJob(request)
        self.jobs[job.id] = job
        self._order.append(job.id)

        async def _wrapped() -> None:
            async with self.semaphore:
                await job.run()

        job._task = asyncio.create_task(_wrapped())
        return job

    def get(self, job_id: str) -> DownloadJob | None:
        return self.jobs.get(job_id)

    def list(self) -> list[JobStatus]:
        return [self.jobs[i].status for i in reversed(self._order) if i in self.jobs]

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.cancel()
        return True

    def remove(self, job_id: str) -> bool:
        """Remove a finished job from the list (does not delete files)."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status.status in ("queued", "running"):
            return False
        self.jobs.pop(job_id, None)
        if job_id in self._order:
            self._order.remove(job_id)
        return True

    def clear_finished(self) -> int:
        """Remove all done/error/cancelled jobs. Returns count removed."""
        to_remove = [jid for jid, j in self.jobs.items()
                     if j.status.status in ("done", "error", "cancelled")]
        for jid in to_remove:
            self.jobs.pop(jid, None)
            if jid in self._order:
                self._order.remove(jid)
        return len(to_remove)


manager = DownloadManager(max_concurrent=config.max_concurrent_downloads)
