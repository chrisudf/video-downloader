from __future__ import annotations

from typing import Optional

from .base import BaseDownloader

REGISTRY: dict[str, type[BaseDownloader]] = {}


def register(cls: type[BaseDownloader]) -> type[BaseDownloader]:
    REGISTRY[cls.name] = cls
    return cls


def get_downloader(name: str) -> BaseDownloader:
    if name not in REGISTRY:
        raise KeyError(f"No downloader registered for {name!r}. Available: {list(REGISTRY)}")
    return REGISTRY[name]()


def route_url(url: str, *, content_hint: Optional[str] = None) -> Optional[type[BaseDownloader]]:
    """Find the first downloader that claims this URL.
    Order matters — register more specific handlers first."""
    for cls in REGISTRY.values():
        if cls.can_handle(url, content_hint=content_hint):
            return cls
    return None
