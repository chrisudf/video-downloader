#!/usr/bin/env bash
# Mac / Linux launcher. Windows users: use run.bat instead.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "[setup] creating virtual environment..."
  "$PY" -m venv .venv
  echo "[setup] installing dependencies..."
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "[setup] installing Playwright Chromium..."
  ./.venv/bin/python -m playwright install chromium
else
  echo "[setup] venv already exists. Skipping install."
fi

echo
echo "[run] starting Video Downloader..."
exec ./.venv/bin/python -m backend.main
