#!/usr/bin/env bash
# Build the macOS .app and .dmg. Run on a Mac with Python 3.10+:
#   ./packaging/build_macos.sh
# Output: dist/VideoDownloader.app and dist/VideoDownloader-macos-<arch>.dmg
#
# The DMG is unsigned/un-notarized (no Apple Developer account wired in).
# A browser-downloaded copy gets quarantined and Gatekeeper reports an
# ad-hoc-signed quarantined app as "damaged" — the unblock is
# `xattr -d com.apple.quarantine`, documented in docs/INSTALL.md. Removing
# the caveat entirely requires Developer ID signing + notarization.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
VENV=.build_venv

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[build] creating build virtualenv..."
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip --quiet
"$VENV/bin/python" -m pip install -r requirements.txt pyinstaller --quiet

echo "[build] running PyInstaller..."
rm -rf dist/VideoDownloader dist/VideoDownloader.app
"$VENV/bin/python" -m PyInstaller packaging/VideoDownloader.spec --noconfirm

APP="dist/VideoDownloader.app"
if [[ ! -d "$APP" ]]; then
  echo "ERROR: $APP was not produced" >&2
  exit 1
fi

# Ad-hoc signature: required on Apple Silicon (unsigned arm64 binaries are
# killed on launch), harmless on Intel. Real signing/notarization can replace
# '-' with a Developer ID later without touching anything else.
echo "[build] ad-hoc codesigning..."
codesign --force --deep --sign - "$APP"

# Arch of the binary actually built, not the host kernel — under Rosetta
# (x86_64 python on an M-series Mac) uname -m still says arm64 and would
# mislabel the DMG.
ARCH="$("$VENV/bin/python" -c 'import platform; print(platform.machine())')"
DMG="dist/VideoDownloader-macos-${ARCH}.dmg"
echo "[build] creating ${DMG}..."
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "Video Downloader" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

echo
echo "[build] done:"
echo "  $APP"
echo "  $DMG"
