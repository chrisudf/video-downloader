from __future__ import annotations

from typing import Optional

from .downloaders.base import BaseDownloader
from .downloaders.registry import REGISTRY, route_url


def pick_downloader(url: str, *, content_hint: Optional[str] = None) -> Optional[type[BaseDownloader]]:
    """Route URL to a downloader. Order in REGISTRY matters —
    youtube is checked first, m3u8 second, fallback handlers last."""
    return route_url(url, content_hint=content_hint)


def list_downloaders() -> list[str]:
    return list(REGISTRY)
