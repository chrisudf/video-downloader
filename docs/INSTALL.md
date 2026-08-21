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

首次启动会自动下载必需组件（yt-dlp / N_m3u8DL-RE / ffmpeg，以及 YouTube 需要的 JS 运行时 deno，约 140 MB），
页面顶部有进度条。下载完成前无法开始下载视频。

**First launch on Windows**: SmartScreen will warn because the installer is
not code-signed. Click **More info → Run anyway**. The app installs per-user
(no admin rights) and auto-downloads its helper tools — including the deno
JS runtime YouTube extraction now requires (~140 MB total) — on first start — progress is shown at the top of the page.

## macOS

1. 下载 `VideoDownloader-macos-arm64.dmg`（M 系列芯片）或
   `VideoDownloader-macos-x86_64.dmg`（旧 Intel 机型），双击打开。
   > 不确定是哪种？点屏幕左上角  → 关于本机：「芯片 Apple M…」选 arm64，「处理器 Intel…」选 x86_64。
2. 把 `VideoDownloader` 拖进 `Applications` 文件夹。
3. **首次打开（只需做一次）**：App 未经 Apple 公证，系统会拦截。按提示文案处理：
   - 提示 **"已损坏，无法打开"**（浏览器下载的最常见情况）：打开 终端
     （启动台搜 "终端" / "Terminal"），把下面整行粘贴进去回车，然后正常双击打开。
     这是唯一一次需要用到终端：

     ```
     xattr -d com.apple.quarantine /Applications/VideoDownloader.app
     ```
   - 提示 **"无法验证开发者"**（较老系统）：右键（按住 Control 点击）图标 → 打开 →
     再点"打开"；或 系统设置 → 隐私与安全性 → 底部点 "仍要打开"。
4. 浏览器会自动打开操作界面。首次启动同样会自动下载所需组件。
   注意：程序在后台运行，**不占 Dock 图标**；要退出请在界面右上 ⚙ 设置里点"退出程序"。

**First launch on macOS** (once only): the app is not notarized. If macOS says
the app **"is damaged"** (the usual verdict for a browser-downloaded copy),
run `xattr -d com.apple.quarantine /Applications/VideoDownloader.app` in
Terminal, then open normally — this is the only step that ever needs a
terminal. On older systems that instead say "unidentified developer",
right-click → Open → Open works. The app runs in the background with no Dock
icon; quit it from the ⚙ Settings dialog in the web UI.

## 用法 / Usage

浏览器界面打开后：粘贴视频页面 URL → **Inspect** → 选清晰度 → 下载。
文件默认保存到 `下载/VideoDownloader` 文件夹，每个任务旁有"打开文件夹"按钮。

Paste a URL → Inspect → pick a quality → Download. Files land in
`Downloads/VideoDownloader`; every job has an "open folder" button.

## 常见问题 / FAQ

**页面关掉了 / 图标点了没反应？**
- Windows：再点一次图标——程序已在运行时会直接再开一个浏览器页。
- macOS：在浏览器地址栏输入 `http://127.0.0.1:8765` 即可回到界面
  （macOS 不会为已运行的程序开新页）。
仍不行就看日志：Windows 在 `%LOCALAPPDATA%\VideoDownloader\logs\app.log`，
macOS 在 `~/Library/Application Support/VideoDownloader/logs/app.log`。

**怎么退出程序？**
界面 ⚙ 设置里点"退出程序"。Windows 升级/卸载时安装器也会自动停掉它。

**组件自动下载失败？**
点横幅上的"重试下载"。仍失败多半是网络问题（GitHub 访问受限时可挂代理再试），
或在 ⚙ 设置里手动填工具路径。

**杀毒软件报警？**
误报常见于未签名的 PyInstaller 程序。代码开源可查。添加信任即可。

**卸载：**
Windows 用"添加或删除程序"；macOS 把 App 拖到废纸篓。
用户数据（配置 + 自动下载的工具）在上面 FAQ 提到的 `VideoDownloader` 数据目录里，可一并删除。
