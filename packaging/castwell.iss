; Current-user installation. The library is external to {app} and is preserved.
#ifndef AppVersion
  #error AppVersion must be supplied by scripts/build_windows.py
#endif
#ifndef SourceDir
  #error SourceDir must be supplied by scripts/build_windows.py
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by scripts/build_windows.py
#endif

[Setup]
AppId={{B38AE33D-2865-4B30-97CF-9887669E948D}
AppName=Castwell
AppVersion={#AppVersion}
AppPublisher=Castwell contributors
AppPublisherURL=https://github.com/mgelsinger/castwell
AppSupportURL=https://github.com/mgelsinger/castwell/issues
AppUpdatesURL=https://github.com/mgelsinger/castwell/releases
DefaultDirName={localappdata}\Programs\Castwell
DefaultGroupName=Castwell
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Castwell-{#AppVersion}-Windows-x64-Setup
SetupIconFile={#SourceDir}\_internal\castwell.ico
UninstallDisplayIcon={app}\Castwell.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Castwell
VersionInfoDescription=Castwell podcast suite installer
UninstallDisplayName=Castwell

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Castwell"; Filename: "{app}\Castwell.exe"; WorkingDir: "{app}"
Name: "{group}\Uninstall Castwell"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Castwell"; Filename: "{app}\Castwell.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Castwell.exe"; Description: "Open Castwell"; Flags: nowait postinstall skipifsilent

; Deliberately no [UninstallDelete] entries for LOCALAPPDATA\Castwell or portable Data.

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\portable.flag')) then
    Result := 'This directory contains a portable Castwell copy. Choose a different application directory. Its Data directory has not been changed.';
end;
