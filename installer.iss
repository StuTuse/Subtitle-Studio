; -*- coding: utf-8; -*-
; Subtitle Studio 安装包脚本（Inno Setup 6）
;
;   编译：ISCC.exe installer.iss
;   产物：installer\SubtitleStudio-<版本>-setup.exe
;
; 设计
; ----
; * 打包 dist\Subtitle Studio 整目录（PyInstaller onedir 产物，~341 MB）。
; * 默认装到用户目录 %LOCALAPPDATA%\Programs\Subtitle Studio：不需要管理员，
;   不写注册表软件键（只写卸载信息与快捷方式），升级/卸载干净。
; * 卸载可选保留用户数据 SSData\（模型缓存/工程/配置默认都在这里）。
; * 桌面快捷方式可选；开始菜单默认。
; * 体检向导（first_run_dialog）会在首次启动自动补装缺的 pip 组件。

#define AppName "Subtitle Studio"
#define AppNameZh "Subtitle Studio 视频字幕工坊"
#define AppExe "Subtitle Studio.exe"
#define AppPublisher "Tuse Creation"
#define SrcDir "dist\Subtitle Studio"

#ifndef AppVersion
; 版本真源 = 根目录 VERSION。ISPP 各版本的字符串/整数比较行为不一致，
; 与其玩预处理技巧，不如由 release.py 发版时用 /DAppVersion= 注入；
; 单独双击编译时回落到这个占位（编译仍成功，只是文件名版本为占位）。
#define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{7E1B6F5A-3D2C-4B8E-9A64-SUBTITLESTU1}
AppName={#AppNameZh}
AppVersion={#AppVersion}
AppVerName={#AppNameZh} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Subtitle Studio
DefaultGroupName={#AppNameZh}
UninstallDisplayName={#AppNameZh}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=installer
OutputBaseFilename=SubtitleStudio-{#AppVersion}-setup
SetupIconFile=assets\app.ico
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
; onedir 产物 341MB，lzma2/max 压缩耗时较长但包小；预计 2-4 分钟
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
ShowLanguageDialog=no
WizardStyle=modern
MinVersion=10.0
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "installer\ChineseSimplified.isl"

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppNameZh}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppNameZh}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppNameZh}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; \
    GroupDescription: "附加任务："; Flags: unchecked

[UninstallDelete]
; 卸载时清掉运行中可能生成的临时文件
Type: files; Name: "{app}\*.log"

[Code]
// 卸载最后一页问一句：要不要连用户数据一起删。
// · {app}\SSData —— 便携模式的数据（模型一下载就是几个 G，默认保留）。
// · %APPDATA%\SubtitleStudio —— config.json 里有**明文 API Key**；
//   %LOCALAPPDATA%\SubtitleStudio —— 音频/模型缓存。默认安装模式数据在
//   这两个目录，早先卸载完全不管，换机/卖电脑时密钥就留在机器上了。
var
  PurgeUserData: Boolean;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Resp: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Resp := MsgBox(
      '要保留字幕模型缓存、配置与工程文件吗？' #13#10 +
      '（配置里存有模型 API Key；模型重新下载要几个 G）' #13#10 +
      '选「是」保留（下次安装可继续用）；选「否」全部删除。',
      mbConfirmation, MB_YESNO) = IDYES;
    PurgeUserData := not Resp;
    if DirExists(ExpandConstant('{app}\SSData')) and PurgeUserData then
      DelTree(ExpandConstant('{app}\SSData'), True, True, True);
    if PurgeUserData then
    begin
      DelTree(ExpandConstant('{userappdata}\SubtitleStudio'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\SubtitleStudio'), True, True, True);
    end;
  end;
end;

// 装完直接启动一次，第一眼就能看到体检向导（若组件缺失）。
// 只走 [Run] 完成页勾选这一条路：早先这里还有一个 ssPostInstall Exec，
// 交互安装时和完成页各启动一次 → 两个实例并发抢写 config.json、
// 弹两个首启向导。
[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppNameZh}"; \
    Flags: nowait postinstall skipifsilent
