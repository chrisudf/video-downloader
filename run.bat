@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv. Is Python installed and on PATH?
        pause
        exit /b 1
    )
    echo [setup] installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
    echo [setup] installing Playwright Chromium...
    ".venv\Scripts\python.exe" -m playwright install chromium
) else (
    echo [setup] venv already exists. Skipping install.
)

echo.
echo [run] starting Video Downloader...
".venv\Scripts\python.exe" -m backend.main
endlocal
