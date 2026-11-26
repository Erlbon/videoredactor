#if 0
; The Windows installer of The Ʌideo Redactor (Inno Setup 6): the one-folder build (dist\videoredactor\, built with
; VIDEOREDACTOR_BUILD=folder; it starts faster than the one-file exe, which unpacks itself at every start) into the
; user's own programs folder, with a Start menu entry. The one-file dist\videoredactor.exe stays the portable download.
; Like the release exe, it does not bundle the external tools (ffmpeg, MKVToolNix): put them in {app}\tools, on
; PATH, or name them in Preferences (README.md).
;
; Build:   build_exe.bat (it builds both programs and passes /DUpdateContract), or by hand:
;          iscc /DAppBuild=2026-11-26#02 /DUpdateContract=<python -m redactor_common.release contract-path> installer\videoredactor.iss
; Output:  dist\videoredactor-setup.exe
;
; Per user, no administrator rights: %LOCALAPPDATA%\Programs\The Video Redactor. Updating goes through the family's
; update framework (redactor_common docs/update-framework.md): its installer contract, included at the END of this
; file, writes {app}\update-channel = windows-installer, so Help > Check for Updates runs the new
; videoredactor-setup.exe the release's update-manifest.json names, silently with /relaunch=yes, and the contract
; starts the program again. The settings, crash log and logs sit beside the exe (the Redactor apps' convention):
; an update leaves them, and so does the uninstall.
;
; These notes sit in "#if 0", as in the shared contract.
#endif

#ifndef AppBuild
  #define AppBuild "0000-00-00#00"
#endif
#ifndef UpdateContract
  #error Pass /DUpdateContract (build_exe.bat does: python -m redactor_common.release contract-path)
#endif

; For the installer contract included at the end. The program has no --quit: Windows' close request ends it.
#define UpdateExe "videoredactor.exe"

[Setup]
AppId={{F93BCAEB-5D48-41F2-B05D-EB6AE2EC81AB}
AppName=The Ʌideo Redactor
AppVersion={#AppBuild}
AppVerName=The Ʌideo Redactor {#AppBuild}
AppPublisher=Erlbon
AppPublisherURL=https://github.com/Erlbon/videoredactor
AppSupportURL=https://github.com/Erlbon/videoredactor/issues
AppUpdatesURL=https://github.com/Erlbon/videoredactor/releases
VersionInfoVersion=0.0.0.0
VersionInfoProductTextVersion={#AppBuild}
PrivilegesRequired=lowest
DefaultDirName={autopf}\The Video Redactor
DefaultGroupName=The Ʌideo Redactor
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\videoredactor.exe
UninstallDisplayName=The Ʌideo Redactor
SetupIconFile=..\assets\icon.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=videoredactor-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "A shortcut on the desktop"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\videoredactor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\The Ʌideo Redactor"; Filename: "{app}\videoredactor.exe"
Name: "{autodesktop}\The Ʌideo Redactor"; Filename: "{app}\videoredactor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\videoredactor.exe"; Description: "Start The Ʌideo Redactor"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; what Help > Check for Updates may leave beside the exe: the old exe after a swap, and a download that did not finish
Type: files; Name: "{app}\videoredactor.exe.old"
Type: files; Name: "{app}\videoredactor.exe.new"
Type: files; Name: "{app}\videoredactor.exe.new.part"

#include UpdateContract
