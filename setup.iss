; 点点 - 桌面连点器 安装程序脚本
#define MyAppName "点点"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Zhgui"
#define MyAppExeName "点点.exe"

[Setup]
AppId={{B7E1F3A2-8C4D-4F1A-9E2D-5B6A7C8D9E0F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=点点
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Messages]
; 欢迎页
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=本程序将引导您完成 [name] 的安装。%n%n建议关闭其他应用程序后再继续。
; 选择目录页
SelectDirLabel=请选择 [name] 的安装位置
SelectDirEditLabel=安装文件夹(&I):
BrowseButton=浏览(&B)...
; 准备安装页
ReadyLabel=安装程序已准备就绪
ReadyLabel2=点击"安装"开始安装，点击"上一步"可重新设置。
; 完成页
FinishedLabel=[name] 安装完成
FinishedLabel2=安装程序已成功在您的计算机上安装 [name]。%n%n点击"完成"退出安装向导。
; 按钮
NextButton=下一步(&N) >
BackButton=< 上一步(&B)
InstallButton=安装(&I)
FinishButton=完成(&F)
CancelButton=取消
; 其他
ConfirmUninstall=您确定要完全删除 [name] 及其所有组件吗？
Uninstalling=正在卸载...

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
