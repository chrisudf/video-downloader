# Packaging（给维护者）

把项目打成非开发者可用的安装包。用户侧说明见 [docs/INSTALL.md](../docs/INSTALL.md)。

## 产物

| 平台 | 命令 | 产物 |
|---|---|---|
| Windows | `packaging\build_windows.bat` | `dist\VideoDownloader-Setup-win64.exe`（装了 Inno Setup 6 时）或 `dist\VideoDownloader-win64.zip` |
| macOS | `./packaging/build_macos.sh` | `dist/VideoDownloader.app` + `dist/VideoDownloader-macos-<arch>.dmg` |

要求：对应平台机器 + Python 3.10+。Windows 安装器需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)（免费；
没装则自动退化为 zip）。macOS 的 DMG 按构建机架构出包——arm64 包发给 M 系列用户，
x86_64 包需在 Intel Mac（或 Rosetta 下的 x86_64 Python）上构建。

调试构建（保留控制台窗口）：设 `VD_CONSOLE=1` 再跑构建脚本。

## 打包版和源码版的差异

- **入口**：`packaging/launcher.py`（multiprocessing freeze 支持 + 无控制台时把
  stdout/stderr 重定向到 `<数据目录>/logs/app.log`）。
- **数据目录**：frozen 时 `config.json`、自动下载的工具、日志都放
  `%LOCALAPPDATA%\VideoDownloader`（Win）/ `~/Library/Application Support/VideoDownloader`（Mac）。
  源码运行行为不变（config.json 仍在项目根）。`VD_DATA_DIR` 环境变量可覆盖（测试用）。
- **外部工具**：不打进安装包（体积 + 许可证 + 更新频率原因）。首次启动
  `backend/bootstrap.py` 自动下载到数据目录并把绝对路径写进 config：
  - yt-dlp：官方 GitHub release 固定名资产
  - N_m3u8DL-RE：GitHub release（经 `releases/latest` 重定向拿 tag，再解析
    `expanded_assets` 页；REST API 只作后备，因为匿名限流 60 次/小时）
  - ffmpeg：Windows 用 BtbN 固定名资产；macOS 用 martin-riedl.de（arm64/x64，
    带稳定 latest 跳转），后备 evermeet.cx（x64）
- **浏览器嗅探**：打包版含 playwright 驱动但不含 170MB 的 Chromium；
  headless / headed 都会回退到用户已装的 Chrome/Edge（`browser_sniff.py`）。
  两者都没有的用户会看到明确报错，不影响 yt-dlp / m3u8 主流程。

## 签名现状（重要）

两个平台都**未签名**，用户首次启动会有系统警告（绕过步骤已写进 INSTALL.md）：

- **Windows**：SmartScreen "已保护你的电脑"。摘掉警告需要 OV/EV 代码签名证书
  （约 $220–690/年）。注意 Azure Trusted Signing 不对澳洲个人/公司开放。
- **macOS**：Gatekeeper 拦截，右键→打开可绕过。摘掉警告需要 Apple Developer
  Program（$99/年）+ notarization。构建脚本里的 ad-hoc 签名（`codesign -s -`）
  只是让 arm64 二进制能启动，不消除警告。将来签名时把 `build_macos.sh` 里的
  `-` 换成 Developer ID，再加 `xcrun notarytool submit` 即可，其他不用动。

## 已知限制

- macOS DMG 一次只出当前架构；想同时发两个包需要两台机器（或 GitHub Actions 矩阵）。
- 安装包没有自定义图标（`.ico`/`.icns` 都没做）——加图标改 spec 的 `icon=` 和
  iss 的 `SetupIconFile` 即可。
- Linux 没做打包（目标用户是 Win/Mac 非开发者；Linux 用户跑 `run.sh`）。
