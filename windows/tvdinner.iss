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
; The [Registry] section below claims URL schemes and (optionally) .m3u, so
; Explorer needs telling to drop its cached associations.
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add tvdinner to PATH (lets you run 'tvdinner' from any Command Prompt)"; Flags: unchecked
; Opt-in: .m3u is a contested type (VLC, MPC, Winamp all want it), so taking it
; silently would be rude. The URL schemes below are ours alone and aren't a task.
Name: "assocm3u"; Description: "Open .m3u / .m3u8 playlists with tvdinner"; Flags: unchecked

[Files]
Source: "..\dist\tvdinner\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion

; Windows URL protocol handlers. Without these, tvtimes' "Play" button and its
; "Open in tvdinner" button do nothing at all on Windows -- the browser hands the
; link to the shell, the shell has never heard of the scheme, and it's dropped
; silently. `tvdinner default-handler` does not cover this: it is Linux-only.
;
;   tvdinner:  one channel, from tvtimes' Play button (24-hour play ticket)
;   tvtimes:   a whole account's export feeds ("Open in tvdinner"), http
;   tvtimess:  the same, https
;
; HKA is HKLM here (this is an admin install into Program Files), so the
; association is machine-wide. %1 is the whole URL, passed as one argument --
; cli.py's _normalize_launch_url is what unwraps and sanitises it.
[Registry]
Root: HKA; Subkey: "Software\Classes\tvdinner"; ValueType: string; ValueName: ""; ValueData: "URL:tvdinner Protocol"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\tvdinner"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\tvdinner\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\tvdinner\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKA; Subkey: "Software\Classes\tvtimes"; ValueType: string; ValueName: ""; ValueData: "URL:tvtimes Protocol"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\tvtimes"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\tvtimes\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\tvtimes\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKA; Subkey: "Software\Classes\tvtimess"; ValueType: string; ValueName: ""; ValueData: "URL:tvtimes Protocol (TLS)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\tvtimess"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\tvtimess\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\tvtimess\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; .m3u / .m3u8, only if the user ticked the task. Registered under our own
; ProgID so unticking (or uninstalling) can't strip another player's entry.
Root: HKA; Subkey: "Software\Classes\tvdinner.playlist"; ValueType: string; ValueName: ""; ValueData: "M3U playlist"; Flags: uninsdeletekey; Tasks: assocm3u
Root: HKA; Subkey: "Software\Classes\tvdinner.playlist\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: assocm3u
Root: HKA; Subkey: "Software\Classes\tvdinner.playlist\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: assocm3u
Root: HKA; Subkey: "Software\Classes\.m3u"; ValueType: string; ValueName: ""; ValueData: "tvdinner.playlist"; Flags: uninsdeletevalue; Tasks: assocm3u
Root: HKA; Subkey: "Software\Classes\.m3u8"; ValueType: string; ValueName: ""; ValueData: "tvdinner.playlist"; Flags: uninsdeletevalue; Tasks: assocm3u

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
