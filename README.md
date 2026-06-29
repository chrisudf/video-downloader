# Video Downloader

本地视频下载工具的 Web UI。粘贴 URL → 自动识别类型 → 列出可选清晰度 → 下载到本地。

## 现已支持
- **YouTube / 1000+ 网站** — 通过 yt-dlp
- **HLS / m3u8** — 通过 N_m3u8DL-RE (自动加 Referer/UA header)

## 留好的扩展接口
- DASH / m4s
- 其他自定义流（实现 `BaseDownloader` 接口即可）

---

## 架构

```
┌─────────────┐   HTTP / WS    ┌────────────────────────────────────┐
│  Frontend   │ ─────────────▶ │           FastAPI Backend          │
│ (HTML+JS)   │  ◀──────────── │  ┌──────────────────────────────┐  │
└─────────────┘   progress     │  │ URLDetector                  │  │
                               │  │  ├─ youtube_dl ext list      │  │
                               │  │  ├─ direct .m3u8?            │  │
                               │  │  └─ html scan for m3u8       │  │
                               │  └──────────────────────────────┘  │
                               │  ┌──────────────────────────────┐  │
                               │  │ DownloaderRegistry           │  │
                               │  │  ├─ YouTubeDownloader  (yt-dlp)│
                               │  │  ├─ M3U8Downloader (N_m3u8DL-RE)│
                               │  │  └─ [future] M4SDownloader   │  │
                               │  └──────────────────────────────┘  │
                               │  ┌──────────────────────────────┐  │
                               │  │ DownloadManager              │  │
                               │  │  · queue · progress · history│  │
                               │  └──────────────────────────────┘  │
                               └────────────────────────────────────┘
                                              │
                                              ▼
                                       D:\Videos  (default)
```

### 关键流程

1. **Inspect** — `POST /api/inspect { url }`
   - Detector 判断类型：YouTube/HTML/直接 m3u8
   - 调用对应 Downloader 的 `probe()` 拿到 title + formats[]
   - 返回 `{ type, title, thumbnail, formats: [{id, label, height, fps, ...}] }`

2. **Download** — `POST /api/download { url, format_id, save_dir? }`
   - 创建 job，进队列，返回 `job_id`
   - 立即开始 subprocess (yt-dlp 或 N_m3u8DL-RE)

3. **Progress** — `WS /ws/progress/{job_id}`
   - 实时推送 stdout 解析后的 `{ percent, speed, eta, status }`

4. **History** — `GET /api/jobs` 列出所有任务

---

## 目录结构

```
video-downloader/
├── backend/
│   ├── main.py              FastAPI 入口
│   ├── config.py            读 config.json
│   ├── models.py            Pydantic schemas
│   ├── detector.py          URL 类型识别 + m3u8 抓取
│   ├── downloads_manager.py 任务队列 + 进度广播
│   └── downloaders/
│       ├── base.py          BaseDownloader 抽象类
│       ├── registry.py      注册表
│       ├── youtube.py       yt-dlp 实现
│       └── m3u8.py          N_m3u8DL-RE 实现
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── config.json              路径、默认下载目录
├── requirements.txt
├── run.bat                  一键启动（建 venv + 启服务 + 开浏览器）
└── README.md
```

---

## TODO

- [x] 项目骨架
- [x] FastAPI 后端 + 配置
- [x] Downloader 插件接口
- [x] YouTube downloader（yt-dlp Python API）
- [x] m3u8 detector（HTML 扫描 + 直链）
- [x] m3u8 downloader（N_m3u8DL-RE subprocess）
- [x] WebSocket 进度推送
- [x] 前端 UI（URL 输入 / 清晰度选 / 进度 / 历史）
- [x] run.bat 启动脚本
- [ ] **后续：** DASH/m4s downloader
- [ ] **后续：** Cookie 导入（下付费/会员视频）
- [ ] **后续：** 字幕下载选项
- [ ] **后续：** 同时多任务并发上限调节

---

## 使用

1. 双击 `run.bat`（首次会自动建虚拟环境并装依赖）
2. 浏览器打开 http://127.0.0.1:8765
3. 粘贴 URL → 点 **Inspect** → 选清晰度 → **Download**

### 配置 (`config.json`)

```json
{
  "save_dir": "D:\\Videos",
  "ytdlp_path": "C:\\yt-dlp.exe",
  "m3u8dl_path": "C:\\N_m3u8DL-RE.exe",
  "ffmpeg_path": "C:\\ffmpeg\\ffmpeg-7.1.1-essentials_build\\ffmpeg-7.1.1-essentials_build\\bin\\ffmpeg.exe",
  "port": 8765
}
```
