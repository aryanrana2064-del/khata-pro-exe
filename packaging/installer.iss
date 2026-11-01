; Inno Setup script. Build AFTER the PyInstaller step:  ISCC packaging\installer.iss
#define AppName "VX7 KHATA PRO"
; The build scripts pass /DAppVersion=<vx7khata.__version__>; this is only the fallback.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppExe "VX7 KHATA PRO.exe"

[Setup]
AppId={{308FA071-0B75-4A75-A8AD-FD2B8F21FB9B}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=VX7_KHATA_PRO_Setup_{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
SetupIconFile=installer\app.ico
WizardImageFile=installer\wizard_164.bmp,installer\wizard_246.bmp,installer\wizard_328.bmp
WizardSmallImageFile=installer\small_55.bmp,installer\small_110.bmp
UninstallDisplayIcon={app}\{#AppExe}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\VX7 KHATA PRO\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks"; Parameters: "/Delete /TN ""VX7 KHATA PRO Automatic Backup"" /F"; Flags: runhidden; RunOnceId: "RemoveBackupTask"

; Customer data lives in %APPDATA%\VX7 KHATA PRO and is deliberately NOT removed on uninstall.
