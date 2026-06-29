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
- [ ] Browser extension that pipes detected `.m3u8` from your normal Chrome tab to the local app (no headless needed)
- [ ] Better autoplay heuristics for JS-heavy sites (more selectors, JS-injected `<video>.play()`)
- [ ] User script (Tampermonkey) variant for the above

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
