[Setup]
; AppId is the upgrade identity — keep it stable at "Dotify" so future
; installs upgrade in place.
AppId=Dotify
AppName=Dotify
AppVersion=0.5.1
AppPublisher=Dotify Project
DefaultDirName={autopf}\Dotify
MinVersion=10.0.22000
ArchitecturesAllowed=x64compatible
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputBaseFilename=DotifySetup
Compression=lzma2
SolidCompression=yes
; App icon: setup.exe carries it, and every installed shortcut plus
; Add/Remove Programs points at the copy build.ps1 stages at the app root.
SetupIconFile=assets\Dotify.ico
UninstallDisplayIcon={app}\Dotify.ico

[Files]
; ignoreversion: the stage is pinned by vendor.lock.json, so it is always the
; set to install. Without it Inno keeps an installed python.exe, node.exe or
; onnxruntime DLL unless the staged one is strictly newer, and a same-version
; rebuild or a deliberate vendor downgrade would ship mixed binaries.
Source: "stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Start Dotify"; Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\launcher.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\Dotify.ico"
Name: "{autoprograms}\Configure Optional API Keys"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\launcher.ps1"" -Configure -NoBrowser"; WorkingDir: "{app}"; IconFilename: "{app}\Dotify.ico"
Name: "{autodesktop}\Start Dotify"; Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\launcher.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\Dotify.ico"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\Speech to text\.env"
