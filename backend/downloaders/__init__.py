from .registry import REGISTRY, register, get_downloader
from . import youtube, m3u8_dl  # noqa: F401 — registers downloaders on import

__all__ = ["REGISTRY", "register", "get_downloader"]
