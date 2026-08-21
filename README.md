# Video Downloader

A local web UI for downloading videos from YouTube, HLS (`.m3u8`) streams, and JavaScript-heavy video pages. Paste a URL → auto-detect → pick a quality → download to disk.

> ⚠️ **Personal use only.** This is a hobby project for downloading videos you have a right to download. See the [Disclaimer](#disclaimer--免责声明) below.

---

## Features

- **YouTube + 1000+ sites** — via [`yt-dlp`](https://github.com/yt-dlp/yt-dlp)
- **HLS / `.m3u8`** — via [`N_m3u8DL-RE`](https://github.com/nilaoda/N_m3u8DL-RE), with custom `Referer` / `User-Agent` headers
- **JS-heavy pages** — Playwright opens a headless (or visible) Chromium and intercepts the actual stream URL from network requests
- **Direct m3u8 input** — paste a known stream URL with optional `Referer` to skip detection
- **Plugin-style downloader registry** — add new formats (DASH `.mpd`, fragmented `.m4s`, etc.) by subclassing `BaseDownloader`
- **Live progress** over WebSocket; queue, cancel, remove, "open folder"
- **Cross-platform** — Windows, macOS, Linux

---

## 安装（非开发者）/ Install (non-developers)

不用装 Python、不用命令行：直接用安装包，见 **[docs/INSTALL.md](docs/INSTALL.md)**。

- **Windows** — `VideoDownloader-Setup-win64.exe`，双击安装
- **macOS** — `VideoDownloader-macos-<arch>.dmg`，拖进 Applications

首次启动自动下载所需组件（yt-dlp / N_m3u8DL-RE / ffmpeg / deno）。
自己构建安装包见 [packaging/README.md](packaging/README.md)。

No Python or terminal needed — grab the installer and follow
[docs/INSTALL.md](docs/INSTALL.md). First launch auto-downloads the helper
tools it needs. To build the installers yourself, see
[packaging/README.md](packaging/README.md).

---

## Quick start (from source)

### Prerequisites

You need **Python 3.10+**. Four external tools do the actual work:

| Tool | Purpose | Install it yourself (optional) |
|---|---|---|
| `yt-dlp` | YouTube etc. | `winget install yt-dlp.yt-dlp` · `brew install yt-dlp` · `pipx install yt-dlp` |
| `N_m3u8DL-RE` | HLS downloader | [Release binary](https://github.com/nilaoda/N_m3u8DL-RE/releases) |
| `ffmpeg` | Muxing audio + video | `winget install Gyan.FFmpeg` · `brew install ffmpeg` · `sudo apt install ffmpeg` |
| `deno` | JS runtime for YouTube | `winget install DenoLand.Deno` · `brew install deno` — `node` or `bun` work too |

**You don't have to install any of them.** On startup the app downloads
whatever is missing into its own data directory and points `config.json` at
the absolute paths — a banner at the top of the page shows per-tool progress.
Anything already on `PATH` is used as-is and never re-downloaded. Set
`auto_download_tools` to `false` to manage the tools yourself.

The one exception is **ffmpeg on Linux**, which has no reliable static-build
source — install it with your package manager.

### Run

```bash
# clone
git clone <this-repo-url>
cd video-downloader

# first time only — copy the example config and edit if needed
cp config.example.json config.json
```

Then:

- **Windows** — double-click `run.bat`
- **macOS / Linux** — `chmod +x run.sh && ./run.sh`

The first run creates a virtualenv, installs Python deps, and downloads Playwright Chromium (~170 MB). Subsequent runs skip all that.

Open <http://127.0.0.1:8765> in your browser.

### Configuration

`config.json` overrides defaults. Paths support `~` for home directory:

```json
{
  "save_dir": "~/Downloads/VideoDownloader",
  "ytdlp_path": "yt-dlp",
  "m3u8dl_path": "N_m3u8DL-RE",
  "ffmpeg_path": "ffmpeg",
  "port": 8765,
  "max_concurrent_downloads": 2,
  "auto_download_tools": true,
  "youtube_player_client": "",
  "js_runtime": "",
  "custom_headers": {
    "Referer": "https://example.com/"
  }
}
```

Settings are also editable from the gear icon in the UI.

#### YouTube extraction

YouTube extraction needs two things beyond yt-dlp itself, and the app manages
both: a **JS runtime** (yt-dlp's challenge solving; without one the usable
formats are never listed) and a **fresh-enough yt-dlp** (the app installs and
updates along yt-dlp's *nightly* channel — the stable channel lags YouTube's
weekly enforcement changes by a month or more, and a month-old stable was
observed failing every real download with HTTP 403 while the same day's
nightly succeeded).

- `youtube_player_client` — empty by default = yt-dlp's own client selection,
  which is correct when the binary is a current nightly. If downloads on your
  network still fail with 403 or "no formats", try `web`, `web_safari` or
  `tv` (one value, not several: yt-dlp merges format lists across listed
  clients, and a selector then matches a format from a client that cannot
  serve it — which client works is network-dependent, so test on yours).
- `js_runtime` — empty auto-detects `deno` → `node` → `bun` on `PATH`, and
  first-run bootstrap installs deno if none is found. Accepts a bare name
  (`deno`), a path to the executable, or yt-dlp's own `name:path` spelling.

#### Where the app keeps its files

Running from source, `config.json` stays next to the code. An installed build
can't write to its own install directory, so it uses a per-user data directory
instead — which is also where auto-downloaded tools and logs go:

| | Data directory |
|---|---|
| Windows | `%LOCALAPPDATA%\VideoDownloader` |
| macOS | `~/Library/Application Support/VideoDownloader` |
| Linux | `$XDG_DATA_HOME/VideoDownloader` (or `~/.local/share/…`) |

Set `VD_DATA_DIR` to override it — useful for a portable install, or for
testing first-run behaviour against a clean directory.

If the app seems not to start, `logs/app.log` in that directory is the first
place to look: installed builds have no console to print to.

#### Custom headers

`custom_headers` are attached to every probe and download — the browser sniff,
yt-dlp, and N_m3u8DL-RE all receive them. Use it for streams that require a
`Referer`, a `Cookie`, or a specific `User-Agent`: self-hosted media servers,
company-internal video, and course platforms that only serve content to a
logged-in session.

In the UI, enter one `Name: Value` per line. Values may contain colons, so
`Referer: https://example.com:8443/watch` works as written.

Precedence, weakest to strongest:

1. Built-in defaults (a desktop `User-Agent`, `Referer` guessed from the
   stream's own origin)
2. `custom_headers`
3. Headers belonging to a specific job — the Referer field in the direct-m3u8
   form, or the one the browser sniff observed for that exact URL

So a configured `Referer` fills in whenever the app would otherwise be
guessing, but never overrides a Referer that was actually observed for the
stream at hand.

> Passing session cookies this way sends them with every request the app
> makes, and they are stored in cleartext in `config.json`. yt-dlp also warns
> that header-passed cookies get scoped to the downloaded URL's domain.
> Prefer a `Referer`/`User-Agent` when that is enough.

#### Playlist pre-flight

Before launching N_m3u8DL-RE, the app fetches the playlist once itself using
exactly the headers the download will use. N_m3u8DL-RE retries an unreachable
manifest 10 times with no flag to disable it, so a dead URL used to cost about
a minute and then report a misleading "failed to download segments". A refusal
is now reported in about a second, with the cause named:

- **403/401 on a URL carrying a signature** (`expires=`, `token=`, `sig=`,
  `X-Amz-Signature=`, …) — reported as an expired or IP-bound signature.
  Retrying and adding headers are both pointless; the URL has to be re-fetched.
- **403/401 on a URL with no signature** — reported as a probable missing
  `Referer`/`Cookie`/`User-Agent`, pointing at the setting above.
- **404/410** — reported as gone.

Only unambiguous client-side rejections abort. Timeouts, DNS and TLS failures,
and 5xx responses fall through to the real downloader, which retries for good
reason. The pre-flight costs one extra HTTP request per download.

Two known limits: a token embedded in the URL *path* rather than the query
string is indistinguishable from a normal path segment, so such a URL gets the
neutral "missing header" message; and a server that rejects `httpx` but accepts
N_m3u8DL-RE would be misreported — which is why only clear 4xx rejections,
reproduced with identical headers, are treated as fatal.

---

## How it works

```
Paste URL ──▶ /api/inspect
                │
                ├─ matches youtube.com / m3u8 / known site
                │        └─▶ probe via yt-dlp or HTTP fetch
                │                  └─▶ list of qualities
                │
                └─ generic page
                         └─ HTML regex scan for .m3u8
                                  └─ if nothing, headless Playwright sniff
                                          └─ if still nothing → suggest manual sniff

Pick quality ──▶ /api/download
                          └─▶ subprocess (yt-dlp or N_m3u8DL-RE)
                                  └─▶ WebSocket progress → UI
                                  └─▶ file lands in save_dir
```

**Plugin interface** — see [`backend/downloaders/base.py`](backend/downloaders/base.py). To support a new format, subclass `BaseDownloader`, implement `can_handle`, `probe`, `download`, and decorate with `@register`. No other files need to change.

---

## Project layout

```
backend/
  main.py                    FastAPI app + routes
  config.py                  Loads config.json with sensible defaults
  appdirs.py                 Resource root vs. per-user data dir; frozen detection
  bootstrap.py               First-run download of the external tools
  models.py                  Pydantic schemas
  detector.py                Routes URLs to downloaders
  downloads_manager.py       Job queue + progress broadcast
  browser_sniff.py           Playwright-based m3u8 sniff
  downloaders/
    base.py                  BaseDownloader abstract class
    registry.py              @register decorator
    youtube.py               yt-dlp wrapper
    m3u8_dl.py               N_m3u8DL-RE wrapper

frontend/
  index.html                 Single-page UI
  style.css
  app.js                     Vanilla JS, no build step

packaging/
  launcher.py                PyInstaller entry point
  VideoDownloader.spec       Shared Windows + macOS build spec
  build_windows.bat          Build the Windows app + installer
  build_macos.sh             Build the macOS .app + .dmg
  windows/VideoDownloader.iss  Inno Setup installer script

docs/INSTALL.md              End-user install guide (bilingual)
run.bat / run.sh             Cross-platform launchers
requirements.txt
config.example.json
```

---

## Roadmap

See [`TODO.md`](TODO.md) for the full punch list of completed work and planned improvements.

---

## Disclaimer / 免责声明

### English

This software is provided **for personal, non-commercial use only**. It is a thin wrapper around well-known open-source tools (`yt-dlp`, `N_m3u8DL-RE`, `ffmpeg`, Playwright) and does not itself bypass DRM or circumvent any technical protection measure.

You are solely responsible for how you use it. Before downloading any content, you must:

1. Verify that you have the legal right to download the material in your jurisdiction (e.g. content you own, public-domain works, content licensed for offline use, or material whose platform's Terms of Service permit personal downloads).
2. Comply with the source platform's Terms of Service.
3. Respect copyright, trademark, privacy, and any other applicable law.

**Commercial use is prohibited.** Do not redistribute downloaded content, do not host it publicly, do not sell it, and do not use this tool as part of a commercial product or service.

The authors and contributors of this project accept **no liability** for any misuse, damages, legal claims, or losses arising from use of this software. Use entirely at your own risk. If you are unsure whether a particular use is legal, **do not use this tool** and consult a lawyer.

### 中文

本软件**仅供个人非商业用途**，本质上是对 `yt-dlp`、`N_m3u8DL-RE`、`ffmpeg`、Playwright 等知名开源工具的封装，本身不破解 DRM，不绕过任何技术保护措施。

使用者对自己的使用行为负完全责任。下载任何内容之前，你必须：

1. 确认在你所在司法管辖区内拥有合法的下载权利（例如：你自己拥有的内容、公有领域作品、明确授权离线使用的内容、平台服务条款允许个人下载的素材）。
2. 遵守源平台的服务条款。
3. 尊重版权、商标、隐私及其他相关法律。

**禁止商业用途。** 不得二次传播下载内容，不得公开托管，不得售卖，不得将本工具用作任何商业产品或服务的一部分。

本项目作者及贡献者对因使用本软件产生的任何**滥用、损害、法律纠纷或损失概不负责**。使用风险自负。如果你不确定某项使用是否合法，**请勿使用本工具**并咨询专业律师。

---

## License

This source code is released under the [MIT License](LICENSE) for personal use, subject to the disclaimer above. The third-party tools it depends on (`yt-dlp`, `N_m3u8DL-RE`, `ffmpeg`, Playwright) are each governed by their own respective licenses.
