from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal, Optional

from .config import config, normalize_headers

# Playwright is heavy — import lazily so the app still starts if it's missing.
try:
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    _PLAYWRIGHT_OK = True
except ImportError:  # pragma: no cover
    _PLAYWRIGHT_OK = False


# URL patterns we consider "media manifests" worth surfacing.
_MEDIA_KEYWORDS = (".m3u8", ".mpd")

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Selectors that often map to "main play" buttons on video pages.
_AUTO_PLAY_SELECTORS = (
    "#video-player-wrap-control",
    ".video-play-control",
    ".vjs-big-play-button",
    ".jw-icon-display",
    ".plyr__control--overlaid",
    "button[aria-label*='Play' i]",
    ".video-play-btn",
    ".play-btn",
)


@dataclass
class SniffedMedia:
    url: str
    referer: Optional[str] = None
    frame_url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class SniffResult:
    page_url: str
    media: list[SniffedMedia]
    iframes: list[str]
    error: Optional[str] = None


def is_available() -> bool:
    return _PLAYWRIGHT_OK


async def sniff(
    url: str,
    *,
    mode: Literal["headless", "headed"] = "headless",
    timeout_seconds: float = 20.0,
    referer: Optional[str] = None,
) -> SniffResult:
    """Open the URL in Chromium, capture media manifest requests.

    `headless`: no window, attempts auto-click on common play buttons.
    `headed`: shows a window — the user clicks play / dismisses popups themselves.
    """
    if not _PLAYWRIGHT_OK:
        return SniffResult(
            page_url=url,
            media=[],
            iframes=[],
            error=(
                "Playwright not installed. Run inside the project venv: "
                "pip install playwright && python -m playwright install chromium"
            ),
        )

    captured: list[SniffedMedia] = []
    seen_urls: set[str] = set()
    iframes_seen: set[str] = set()

    async with async_playwright() as p:
        launch_args = ["--autoplay-policy=no-user-gesture-required", "--mute-audio"]
        headless = (mode == "headless")
        # Bundled Chromium's chrome.exe sometimes fails to launch on Windows
        # ("spawn UNKNOWN" / SxS manifest errors). Prefer the user's installed
        # Chrome → Edge → bundled chromium-headless-shell, in that order.
        launch_attempts: list[dict] = []
        if not headless:
            launch_attempts.append({"channel": "chrome", "headless": False, "args": launch_args})
            launch_attempts.append({"channel": "msedge", "headless": False, "args": launch_args})
        launch_attempts.append({"headless": headless, "args": launch_args})

        browser = None
        last_err: Optional[Exception] = None
        for kwargs in launch_attempts:
            try:
                browser = await p.chromium.launch(**kwargs)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        if browser is None:
            raise RuntimeError(f"Could not launch any browser. Last error: {last_err}")

        try:
            context_kwargs: dict = {
                "user_agent": _DEFAULT_UA,
                "viewport": {"width": 1280, "height": 720},
                "bypass_csp": True,
            }
            extra_headers: dict[str, str] = {}
            if referer:
                extra_headers["Referer"] = referer
            # Apply the headers configured in Settings, so a page that needs a
            # Cookie/Referer to play can be sniffed at all — and so the
            # Referer we observe matches what the downloader will later send.
            for k, v in normalize_headers(config.headers()).items():
                # Playwright takes the UA as its own context argument;
                # setting it via extra_http_headers as well is ignored for
                # navigator.userAgent and would desync JS-side fingerprinting.
                if k.lower() == "user-agent":
                    context_kwargs["user_agent"] = v
                else:
                    extra_headers[k] = v
            if extra_headers:
                context_kwargs["extra_http_headers"] = extra_headers
            context = await browser.new_context(**context_kwargs)

            done = asyncio.Event()

            def _on_request(req) -> None:
                u = req.url
                low = u.lower()
                if not any(k in low for k in _MEDIA_KEYWORDS):
                    return
                if u in seen_urls:
                    return
                seen_urls.add(u)
                # Only surface master playlists / variant manifests; skip
                # segment .ts/.m4s which would flood the list.
                if low.endswith((".ts", ".m4s")):
                    return
                captured.append(
                    SniffedMedia(
                        url=u,
                        referer=req.headers.get("referer"),
                        frame_url=req.frame.url if req.frame else None,
                        headers={k: v for k, v in req.headers.items()
                                 if k.lower() in ("user-agent", "referer", "origin", "cookie")},
                    )
                )
                # Headless: stop early as soon as we have something.
                if mode == "headless":
                    done.set()

            context.on("request", _on_request)

            page = await context.new_page()
            page.on("frameattached", lambda f: iframes_seen.add(f.url or ""))
            page.on("framenavigated", lambda f: iframes_seen.add(f.url or ""))
            # User closing the window / browser → bail out immediately instead
            # of waiting the full timeout_seconds.
            page.on("close", lambda _p: done.set())
            context.on("close", lambda _c: done.set())
            browser.on("disconnected", lambda _b: done.set())

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                await browser.close()
                return SniffResult(page_url=url, media=[], iframes=[], error=f"navigation failed: {e}")

            # Initial settle
            await page.wait_for_timeout(1500)

            if mode == "headless":
                # Best-effort: try a few common selectors to wake the player up
                for sel in _AUTO_PLAY_SELECTORS:
                    if done.is_set():
                        break
                    try:
                        await page.click(sel, force=True, timeout=1500)
                        break
                    except Exception:
                        continue

                # Walk into child frames and try to autoplay any <video>
                async def poke_frames() -> None:
                    for f in page.frames:
                        if f == page.main_frame:
                            continue
                        try:
                            await f.evaluate(
                                """() => {
                                    document.querySelectorAll('video').forEach(v => {
                                        v.muted = true;
                                        v.play().catch(()=>{});
                                    });
                                }"""
                            )
                        except Exception:
                            pass

                # Wait up to timeout_seconds, polling frames every second
                deadline = asyncio.get_event_loop().time() + timeout_seconds
                while not done.is_set() and asyncio.get_event_loop().time() < deadline:
                    await poke_frames()
                    try:
                        await asyncio.wait_for(done.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        pass
            else:
                # Headed: just wait for the user to click play. Resolve when
                # any media is captured OR the timeout elapses.
                try:
                    await asyncio.wait_for(done.wait(), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    pass

            # Final small grace period for any in-flight requests.
            # Page may already be closed by the user — ignore that.
            try:
                await page.wait_for_timeout(500)
            except Exception:
                pass

        finally:
            try:
                await browser.close()
            except Exception:
                pass

    if captured:
        err = None
    elif mode == "headed":
        err = "browser closed (or timed out) before any m3u8 was captured"
    else:
        err = "no media manifest captured"
    return SniffResult(
        page_url=url,
        media=captured,
        iframes=sorted(u for u in iframes_seen if u and not u.startswith("about:")),
        error=err,
    )
