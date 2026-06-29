# TODO

## ✅ Done

### Core architecture
- [x] FastAPI backend with plugin-style downloader registry
- [x] `BaseDownloader` abstract class — implement once, register, route picks it up
- [x] Pydantic models for inspect / download / job status
- [x] Cross-platform config (Windows / macOS / Linux, with `~` path expansion)

### Downloaders
- [x] **YouTube** (and 1000+ yt-dlp-supported sites)
  - Probe via `yt-dlp` Python API for metadata
  - Download via `yt-dlp.exe` subprocess (avoids the PO-token issue in the latest pip package)
  - Preset quality menu: Best / 1080p / 720p / 480p / 360p / 240p / 144p / audio-only
- [x] **HLS / `.m3u8`**
  - Parses master playlist for variants (resolution, fps, bandwidth)
  - Downloads via `N_m3u8DL-RE` with custom `Referer` / `User-Agent` headers
  - Handles single-stream media playlists too
- [x] Generic-page HTML scan (regex for `.m3u8` URLs in source)

### Browser sniff
- [x] Headless Playwright fallback when no direct match (auto)
- [x] Headed mode — opens visible Chromium for manual play/ad-dismiss
- [x] Launch fallback chain: system Chrome → Edge → bundled Chromium
- [x] Detects user closing browser window — returns immediately instead of waiting timeout
- [x] Windows: `ProactorEventLoop` policy fix for subprocess spawn

### Jobs
- [x] In-memory job queue with configurable concurrency limit
- [x] WebSocket per-job progress stream
- [x] Cancel running jobs
- [x] Remove finished jobs (single + bulk "clear finished")
- [x] Reveal in OS file manager (Windows Explorer / macOS Finder / Linux xdg-open)

### Frontend
- [x] Single-page vanilla HTML/CSS/JS, no build step
- [x] URL inspect → quality picker → download flow
- [x] Direct m3u8 input panel (paste URL + optional Referer)
- [x] Live progress bars, speed, ETA, log tail
- [x] Settings dialog (save dir, tool paths, concurrency)
- [x] Dark theme

### Distribution / quality
- [x] `run.bat` (Windows) and `run.sh` (macOS / Linux) launchers
- [x] First-run auto-creates venv + installs deps + Playwright Chromium
- [x] `.gitignore` excludes local config, virtual env, downloaded artifacts

---

## 🚧 Future / nice-to-have

### More formats / sites
- [ ] **DASH / `.mpd`** downloader (`N_m3u8DL-RE` already supports it; add an `MPDDownloader` class + URL pattern)
- [ ] **Fragmented `.m4s`** (CMAF) — similar to DASH
- [ ] Bilibili-specific extractor with cookie support (B站会员视频)
- [ ] Twitter/X video extractor with auth
- [ ] Twitch VOD downloader

### Authentication / cookies
- [ ] Browser cookie import (read from Chrome/Edge/Firefox profile, like `yt-dlp --cookies-from-browser`)
- [ ] Per-site stored credentials (encrypted)
- [ ] Cookie field in the direct-m3u8 form

### Downloads
- [ ] Subtitles selection (currently always skipped)
- [ ] Audio-track selection for multi-track videos
- [ ] Playlist support — yt-dlp can return playlists; expose a "select episodes" UI
- [ ] Resume interrupted downloads (yt-dlp can; m3u8 partial resume is harder)
- [ ] Post-download hooks (auto-rename, move to category folder, run a custom script)

### Sniff improvements

#### ⭐ Chrome extension (cat-catch–style companion) — top priority
The current Playwright sniff has to spawn a separate Chromium and either autoplay or wait for the user to click play in an unfamiliar window. Cleaner alternative: a Manifest V3 extension that lives in the user's normal Chrome and pipes detected `.m3u8` to this app.

Why it's better than Playwright for most cases:
- No extra process — uses the browser the user already has open
- Inherits the user's logged-in session and cookies (works for paid/VIP content)
- No autoplay simulation — the human just clicks play normally
- Invisible to anti-bot heuristics that fingerprint headless Chromium
- Faster end-to-end (no Chromium cold start)

Design sketch:
1. **Extension** (`extension/` directory in repo)
   - `manifest.json` (MV3, permissions: `webRequest`, `host_permissions: ["<all_urls>"]`)
   - `background.js` — listen to `chrome.webRequest.onBeforeRequest`, filter URLs containing `.m3u8` or `.mpd`, dedupe per tab
   - `popup.html` + `popup.js` — list captured URLs for the current tab with a "Download" button next to each
   - Button POSTs `{ url, referer, downloader: "m3u8" }` to `http://127.0.0.1:8765/api/download`
2. **Backend changes**
   - Add CORS allowlist for `chrome-extension://<our-id>` on `/api/download` and `/api/inspect`
   - Optional: a "discovered URLs" inbox endpoint so the extension can pre-stage suggestions in the UI even if the user doesn't click the extension popup
3. **Distribution**
   - Initially: load unpacked from `extension/` (devloader)
   - Later: publish to Chrome Web Store; same code works on Edge

Keep the Playwright path as the fallback for headless / scripted use.

#### Other
- [ ] Better autoplay heuristics for JS-heavy sites (more selectors, JS-injected `<video>.play()`)
- [ ] User script (Tampermonkey) variant of the extension for browsers without extension support

### UI / UX
- [ ] Drag-and-drop a URL onto the window
- [ ] Persist job history across restarts (SQLite)
- [ ] Bandwidth / speed throttling per job
- [ ] Pause / resume controls
- [ ] System tray icon (background-run mode)
- [ ] Mobile-friendly responsive layout
- [ ] i18n (currently mixed Chinese/English UI)

### Quality / robustness
- [ ] Unit tests for URL routing, format parsing, progress regex
- [ ] CI for both Windows and macOS runners
- [ ] Type checking with `mypy --strict`
- [ ] Pre-commit hook (ruff + black)
- [ ] Graceful handling of locked output files (Windows OneDrive sync)

### Packaging
- [ ] Single-binary build via PyInstaller for non-technical users
- [ ] Homebrew tap for macOS
- [ ] WinGet manifest for Windows
- [ ] Docker image for headless server use

### Not-doing (explicit non-goals)
- ❌ DRM-protected content (Widevine / FairPlay / PlayReady) — out of scope, illegal in most jurisdictions
- ❌ Bulk-scraping a site's entire catalog — encourages abuse; if you have legitimate need, use the platform's own API
- ❌ Cloud hosting / multi-user / SaaS — this is a personal-use desktop tool; deploying it publicly creates abuse risk and legal exposure
- ❌ Removing watermarks / re-encoding to disguise origin
