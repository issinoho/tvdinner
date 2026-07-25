; Inno Setup script for the Windows installer.
;
; Built by .github/workflows/release.yml's build-windows job as:
;   iscc windows\tvdinner.iss /DMyAppVersion=<version>
;
; Expects windows\tvdinner.spec to have already been run (via
; `pyinstaller windows\tvdinner.spec` from the repo root), producing
; dist\tvdinner\ -- the onedir bundle this packages up.

#define MyAppName "tvdinner"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Iain Smith"
#define MyAppURL "https://github.com/issinoho/tvdinner"
#define MyAppExeName "tvdinner.exe"

[Setup]
; Fixed AppId (do not change) -- lets Windows/Inno Setup recognize
; upgrades of the same install rather than treating each version as an
; unrelated program.
AppId={{7B591A96-E431-4BB4-B509-3EAFD9CFA81F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputBaseFilename=tvdinner-setup-{#MyAppVersion}
OutputDir=..\dist_installer
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add tvdinner to PATH (lets you run 'tvdinner' from any Command Prompt)"; Flags: unchecked

[Files]
Source: "..\dist\tvdinner\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Code]
const
  EnvironmentKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

// Adds Path to the machine-wide PATH environment variable, unless it's
// already present. Takes effect in new processes/shells (e.g. after
// logging back in), not ones already open.
procedure EnvAddPath(Path: string);
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Paths) then
    Paths := '';
  if Pos(';' + Uppercase(Path) + ';', ';' + Uppercase(Paths) + ';') > 0 then
    exit;
  if (Length(Paths) > 0) and (Paths[Length(Paths)] <> ';') then
    Paths := Paths + ';';
  Paths := Paths + Path;
  RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Paths);
end;

// Rebuilds PATH with any segment matching Path (case-insensitively)
// removed, rather than a substring Delete() -- safer against segments
// that are prefixes/suffixes of each other or of Path itself.
procedure EnvRemovePath(Path: string);
var
  Paths, Segment, Rebuilt: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Paths) then
    exit;
  Rebuilt := '';
  while Length(Paths) > 0 do
  begin
    P := Pos(';', Paths);
    if P = 0 then
    begin
      Segment := Paths;
      Paths := '';
    end
    else
    begin
      Segment := Copy(Paths, 1, P - 1);
      Paths := Copy(Paths, P + 1, Length(Paths));
    end;
    if (Segment <> '') and (CompareText(Segment, Path) <> 0) then
    begin
      if Rebuilt <> '' then
        Rebuilt := Rebuilt + ';';
      Rebuilt := Rebuilt + Segment;
    end;
  end;
  RegWriteExpandStringValue(HKEY_LOCAL_MACHINE, EnvironmentKey, 'Path', Rebuilt);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    EnvAddPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    EnvRemovePath(ExpandConstant('{app}'));
end;
