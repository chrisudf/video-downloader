@echo off
REM Build the Windows app + installer. Run on Windows with Python 3.10+:
REM   packaging\build_windows.bat
REM Output: dist\VideoDownloader\ (onedir app)
REM         dist\VideoDownloader-Setup-win64.exe  (if Inno Setup 6 installed)
REM         dist\VideoDownloader-win64.zip        (fallback when it is not)
REM
REM Inno Setup: https://jrsoftware.org/isdl.php (free). Add ISCC.exe to PATH
REM or install to the default location.
setlocal
cd /d "%~dp0.."

if not exist ".build_venv\Scripts\python.exe" (
    echo [build] creating build virtualenv...
    python -m venv .build_venv
    if errorlevel 1 (
        echo Failed to create venv. Is Python 3.10+ installed and on PATH?
        exit /b 1
    )
)
".build_venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".build_venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

echo [build] running PyInstaller...
if exist "dist\VideoDownloader" rmdir /s /q "dist\VideoDownloader"
".build_venv\Scripts\python.exe" -m PyInstaller packaging\VideoDownloader.spec --noconfirm
if errorlevel 1 exit /b 1

set ISCC=
where ISCC.exe >nul 2>nul && set ISCC=ISCC.exe
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if defined ISCC (
    echo [build] building installer with Inno Setup...
    "%ISCC%" packaging\windows\VideoDownloader.iss
    if errorlevel 1 exit /b 1
) else (
    echo [build] Inno Setup not found - creating portable zip instead.
    powershell -NoProfile -Command "Compress-Archive -Path 'dist\VideoDownloader\*' -DestinationPath 'dist\VideoDownloader-win64.zip' -Force"
)

echo.
echo [build] done. See the dist\ folder.
endlocal
