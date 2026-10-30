; Inno Setup script for the Parallel installer. Build with:  ISCC.exe tools\Parallel.iss
; Needs dist\Parallel (run build_parallel.ps1 first). Output: dist\installer\Parallel-Setup.exe
#define AppName "Parallel"
#define AppVersion "1.0.0"
#define AppPublisher "Chukway"

[Setup]
AppId={{6B1D6F3A-5C1E-4C57-9B0A-3F1E7A2D8C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=Parallel-Setup
SetupIconFile=..\Parallel.ico
UninstallDisplayIcon={app}\Parallel.exe
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Parallel\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Parallel.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Parallel.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Parallel.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

; Uninstalling removes the program only. Items, listings and settings live in
; %LOCALAPPDATA%\Parallel and are kept on purpose.
