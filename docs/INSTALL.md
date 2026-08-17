# 安装指南 / Install Guide

给没有开发经验的用户。不需要安装 Python，不需要命令行。

For non-technical users. No Python, no terminal.

---

## Windows

1. 下载 `VideoDownloader-Setup-win64.exe`，双击运行。
2. **SmartScreen 提示"Windows 已保护你的电脑"是正常的**（安装包没有购买代码签名证书）：
   点 **更多信息 (More info)** → **仍要运行 (Run anyway)**。
3. 一路下一步。安装到你自己的用户目录，不需要管理员权限。
4. 完成后自动启动，浏览器会打开 `http://127.0.0.1:8765` 的操作界面。

首次启动会自动下载三个必需组件（yt-dlp / N_m3u8DL-RE / ffmpeg，约 100 MB），
页面顶部有进度条。下载完成前无法开始下载视频。

**First launch on Windows**: SmartScreen will warn because the installer is
not code-signed. Click **More info → Run anyway**. The app installs per-user
(no admin rights) and auto-downloads its three helper tools (~100 MB) on
first start — progress is shown at the top of the page.

## macOS

1. 下载 `VideoDownloader-macos-arm64.dmg`（M 系列芯片）或
   `VideoDownloader-macos-x86_64.dmg`（旧 Intel 机型），双击打开。
   > 不确定是哪种？点屏幕左上角  → 关于本机：「芯片 Apple M…」选 arm64，「处理器 Intel…」选 x86_64。
2. 把 `VideoDownloader` 拖进 `Applications` 文件夹。
3. **首次打开必须：右键（或按住 Control 点击）图标 → 打开 → 再点"打开"**。
   直接双击会提示"无法打开，因为无法验证开发者"——这是因为 App 未经 Apple 公证，
   右键打开是 Apple 官方提供的绕过方式，只需要做一次。
4. 浏览器会自动打开操作界面。首次启动同样会自动下载三个组件。

**First launch on macOS**: the app is not notarized, so double-clicking is
blocked. **Right-click the app → Open → Open** (needed once only). If macOS
still refuses（新版系统可能需要）: 打开 系统设置 → 隐私与安全性，页面底部会出现
"仍要打开 / Open Anyway" 按钮。

## 用法 / Usage

浏览器界面打开后：粘贴视频页面 URL → **Inspect** → 选清晰度 → 下载。
文件默认保存到 `下载/VideoDownloader` 文件夹，每个任务旁有"打开文件夹"按钮。

Paste a URL → Inspect → pick a quality → Download. Files land in
`Downloads/VideoDownloader`; every job has an "open folder" button.

## 常见问题 / FAQ

**页面打不开 / 图标点了没反应？**
再点一次图标——如果程序已在运行，会直接再打开一个浏览器页。
Windows 日志在 `%LOCALAPPDATA%\VideoDownloader\logs\app.log`，
macOS 在 `~/Library/Application Support/VideoDownloader/logs/app.log`。

**组件自动下载失败？**
点横幅上的"重试下载"。仍失败多半是网络问题（GitHub 访问受限时可挂代理再试），
或在 ⚙ 设置里手动填三个工具的路径。

**杀毒软件报警？**
误报常见于未签名的 PyInstaller 程序。代码开源可查。添加信任即可。

**卸载：**
Windows 用"添加或删除程序"；macOS 把 App 拖到废纸篓。
用户数据（配置 + 自动下载的工具）在上面 FAQ 提到的 `VideoDownloader` 数据目录里，可一并删除。
