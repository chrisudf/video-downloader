from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class FormatOption(BaseModel):
    """One selectable quality option."""
    id: str
    label: str
    height: Optional[int] = None
    fps: Optional[float] = None
    ext: Optional[str] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    filesize: Optional[int] = None
    note: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class InspectResult(BaseModel):
    """Returned by /api/inspect — what a URL has."""
    type: Literal["youtube", "m3u8", "generic", "unknown"]
    source_url: str
    resolved_url: Optional[str] = None
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[float] = None
    uploader: Optional[str] = None
    formats: list[FormatOption] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    downloader: str
    raw: dict[str, Any] = Field(default_factory=dict)


class InspectRequest(BaseModel):
    url: str
    referer: Optional[str] = None


class DownloadRequest(BaseModel):
    url: str
    format_id: str
    downloader: str
    title: Optional[str] = None
    save_dir: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


class JobStatus(BaseModel):
    job_id: str
    url: str
    title: Optional[str] = None
    downloader: str
    format_id: str
    status: Literal["queued", "running", "done", "error", "cancelled"]
    percent: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    save_dir: str
    output_file: Optional[str] = None
    error: Optional[str] = None
    log_tail: list[str] = Field(default_factory=list)
    created_at: float
    finished_at: Optional[float] = None
