; Inno Setup script for the Klotse installer. Built by packaging/windows/build.ps1:
;   iscc /DAppVersion=0.1.0 /DNumericVersion=0.1.0 /DIconFile=build\icons\klotse.ico packaging\windows\klotse.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Klotse"
#endif
#ifndef IconFile
  #define IconFile "..\..\build\icons\klotse.ico"
#endif

[Setup]
; AppId identifies this program for upgrades and uninstall. Never change it once a release has been published.
AppId={{0B478781-E098-4C06-9C3D-6A2BD1B4199C}
AppName=Klotse
AppVersion={#AppVersion}
AppVerName=Klotse {#AppVersion}
AppPublisher=Marcelo H. Bartsch
AppPublisherURL=https://github.com/mbartsch/klotse
AppSupportURL=https://github.com/mbartsch/klotse/issues
AppUpdatesURL=https://github.com/mbartsch/klotse/releases
VersionInfoVersion={#NumericVersion}
DefaultDirName={autopf}\Klotse
DefaultGroupName=Klotse
DisableProgramGroupPage=yes
; Installs for the current user without administrator rights; the wizard offers "all users" as an option.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Klotse.exe
OutputDir=..\..\dist
OutputBaseFilename=Klotse-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Klotse"; Filename: "{app}\Klotse.exe"
Name: "{autodesktop}\Klotse"; Filename: "{app}\Klotse.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Klotse.exe"; Description: "{cm:LaunchProgram,Klotse}"; Flags: nowait postinstall skipifsilent
