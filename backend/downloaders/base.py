from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional

from ..models import DownloadRequest, InspectResult


@dataclass
class ProgressEvent:
    """A single progress tick streamed back from a downloader."""
    percent: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    status: str = "running"   # running | done | error
    message: Optional[str] = None
    output_file: Optional[str] = None


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class BaseDownloader(ABC):
    """All downloaders implement this. Add a new streaming format by
    subclassing this and calling `register(name, cls)`."""

    name: str = "base"

    @classmethod
    @abstractmethod
    def can_handle(cls, url: str, *, content_hint: Optional[str] = None) -> bool:
        """Quick URL match. Cheap — used for routing."""

    @abstractmethod
    async def probe(self, url: str, *, referer: Optional[str] = None) -> InspectResult:
        """Inspect URL and return available formats."""

    @abstractmethod
    async def download(
        self,
        request: DownloadRequest,
        on_progress: ProgressCallback,
        cancel_event: asyncio.Event,
    ) -> str:
        """Run the download. Yields progress via `on_progress`.
        Returns the absolute path to the resulting file.
        Must respect `cancel_event`."""
