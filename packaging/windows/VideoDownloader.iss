; Inno Setup 6 script for Video Downloader.
; Built by packaging\build_windows.bat after PyInstaller produces
; dist\VideoDownloader\. Compile manually with:
;   ISCC.exe packaging\windows\VideoDownloader.iss

#define MyAppName "Video Downloader"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "chrisudf"
#define MyAppURL "https://github.com/chrisudf/video-downloader"
#define MyAppExeName "VideoDownloader.exe"

[Setup]
; Stable GUID so upgrades replace the existing install instead of duplicating.
AppId={{7C1F2A9E-3B64-4F0D-9A57-2E8B41C6D0F3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install: no UAC prompt, no admin needed, and the app can update
; its own tools under %LOCALAPPDATA% without permission errors.
PrivilegesRequired=lowest
DefaultDirName={autopf}\VideoDownloader
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=VideoDownloader-Setup-win64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The app is a windowless background server — there is no window to receive a
; polite close request, so force-terminate it via Restart Manager before
; replacing files on upgrade.
CloseApplications=force
; "x64" (not the newer "x64compatible") so the script compiles on the still
; widespread Inno Setup 6.0-6.2; 6.3+ accepts it too (with a deprecation
; warning). The PyInstaller output is x64-only anyway.
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; A PyInstaller onedir tree must never be overlaid: files renamed or removed
; between builds (python DLL bumps, dependency upgrades, stale dist-info)
; would linger and break imports after an upgrade. Clear it first — the
; per-user data in %LOCALAPPDATA%\VideoDownloader is outside {app} and safe.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\VideoDownloader\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; The server has no window; make sure uninstall can delete the exe even while
; it is running.
Filename: "{cmd}"; Parameters: "/C taskkill /f /im {#MyAppExeName}"; Flags: runhidden; RunOnceId: "KillServer"
