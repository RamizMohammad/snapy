; Snapy — Inno Setup script
; Built by ..\build.ps1, which freezes the app first. Compile by hand with:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\snapy.iss

#define AppName        "Snapy"
#define AppVersion     "1.0.0"
#define AppPublisher   "Loopax Technologies"
#define AppURL         "https://mohammadramiz.in"
#define AppExe         "Snapy.exe"

[Setup]
AppId={{9E2C1A54-7B36-4D9A-9C21-5F0E4A8D7C13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Android snapshot and restore

; Per-user by default so no UAC prompt; the user can choose all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes

OutputDir=Output
OutputBaseFilename=Snapy-{#AppVersion}-setup
SetupIconFile=..\assets\snapy.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes

; PyInstaller output is 64-bit; refuse to install on 32-bit Windows.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Everything PyInstaller produced, including the bundled platform-tools.
Source: "..\dist\Snapy\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";             Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";       Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings live in the user profile, not the install folder — remove them too.
Type: filesandordirs; Name: "{localappdata}\Snapy"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Snapy carries its own adb, so there is nothing to add to PATH.
      Left as a hook in case a future build wants to register one. }
  end;
end;
