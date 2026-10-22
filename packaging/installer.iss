#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif
[Setup]
AppId={{64632E05-125D-47FC-82C6-B9C92FBC3D6A}
AppName=Ege Ders Downloader
AppVersion={#AppVersion}
AppPublisher=Gehrman-Sparrow42
AppPublisherURL=https://github.com/Gehrman-Sparrow42/egeders-downloader
DefaultDirName={localappdata}\Programs\EgeDersDownloader
DefaultGroupName=Ege Ders Downloader
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=EgeDersDownloader-{#AppVersion}-Setup-x64
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\EgeDersDownloader.exe
CloseApplications=yes
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "..\dist\EgeDersDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Ege Ders Downloader"; Filename: "{app}\EgeDersDownloader.exe"
Name: "{group}\Uninstall Ege Ders Downloader"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Ege Ders Downloader"; Filename: "{app}\EgeDersDownloader.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\EgeDersDownloader.exe"; Description: "Open Ege Ders Downloader"; Flags: nowait postinstall skipifsilent
