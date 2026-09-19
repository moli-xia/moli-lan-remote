; 魔力局域网远程助手 — Inno Setup 安装包脚本(v1.0.0)
; 构建:ISCC.exe packaging\setup.iss (先运行 PyInstaller 生成 dist\MoliLanRemote)

#define MyAppName "魔力局域网远程助手"
#define MyAppVersion "1.0.0"

[Setup]
AppId={{9C2F5A31-47B8-4E6D-8A5F-MOLIREMOTE01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=moli-xia
AppPublisherURL=https://github.com/moli-xia/moli-lan-remote
DefaultDirName={autopf}\MoliLanRemote
DefaultGroupName=魔力局域网远程助手
PrivilegesRequired=admin
OutputDir=dist
OutputBaseFilename=MoliLanRemote-Setup-1.0.0
SetupIconFile=icons\host.ico
UninstallDisplayIcon={app}\MoliHost.exe
LicenseFile=license.txt
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(被控端 + 主控端)"; \
    GroupDescription: "附加任务:"; Flags: checkedonce
Name: "firewall"; Description: "Windows 防火墙放行魔力局域网远程助手(强烈推荐)"; \
    GroupDescription: "附加任务:"; Flags: checkedonce
Name: "autostart"; Description: "开机自动启动被控端(本机作为被控机时推荐)"; \
    GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "..\dist\MoliLanRemote\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\魔力被控端(共享本机屏幕)"; Filename: "{app}\MoliHost.exe"
Name: "{group}\魔力主控端(控制其他设备)"; Filename: "{app}\MoliViewer.exe"
Name: "{group}\卸载魔力局域网远程助手"; Filename: "{uninstallexe}"
Name: "{autodesktop}\魔力被控端"; Filename: "{app}\MoliHost.exe"; Tasks: desktopicon
Name: "{autodesktop}\魔力主控端"; Filename: "{app}\MoliViewer.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "MoliHost"; \
    ValueData: """{app}\MoliHost.exe"""; Tasks: autostart; \
    Flags: uninsdeletevalue

[Run]
Filename: "netsh"; \
    Parameters: "advfirewall firewall add rule name=""魔力局域网远程助手"" dir=in action=allow program=""{app}\MoliHost.exe"" enable=yes profile=any"; \
    Tasks: firewall; Flags: runhidden waituntilterminated
Filename: "{app}\MoliHost.exe"; \
    Description: "启动魔力被控端"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""魔力局域网远程助手"""; \
    Flags: runhidden; RunOnceId: "DelFwRule"
