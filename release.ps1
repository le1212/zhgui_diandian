<#
点点 一键发版脚本：自动完成"改版本号之后"的全部机械步骤。

流程：
  1. 运行单元测试（发版前兜底）
  2. 读取 version.py 首行的 APP_VERSION（版本号唯一来源）
  3. 构建绿色版（PyInstaller）与安装版（Inno Setup，版本号自动注入）
  4. 计算安装包 SHA-256 与字节数，生成 deploy/updates/latest.json
  5. 打印剩余的人工步骤（上传安装包、发布官网）

用法：
  .\release.ps1 -Notes "本次更新说明"              # 完整发版
  .\release.ps1 -Notes "..." -MinVersion "1.2.0"   # 附带强制更新门槛
  .\release.ps1 -SkipBuild -DryRun                 # 只重新生成清单，预演不写文件

发布后老用户启动点点即会收到更新横幅（24 小时节流）。
#>
param(
    [string]$Notes,
    [string]$MinVersion = "",
    [string]$PageUrl = "https://dd.zhigui.icu/",
    # 安装包专用下载域（仅 DNS 直连源站，不挂 EdgeOne），diandian 为点点专属子目录
    [string]$DownloadBase = "https://install.zhigui.icu/diandian",
    [switch]$SkipBuild,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "==> 运行单元测试..."
python -m pytest (Join-Path $root "tests") -q
if ($LASTEXITCODE -ne 0) { throw "单元测试未通过，终止发版" }

# 版本号唯一来源：version.py 首行，与 setup.iss 的 ISPP 解析规则一致
$versionLine = Get-Content (Join-Path $root "version.py") -TotalCount 1 -Encoding UTF8
if ($versionLine -notmatch 'APP_VERSION\s*=\s*"(\d+(?:\.\d+)+)"') {
    throw "无法从 version.py 首行解析 APP_VERSION：$versionLine"
}
$version = $Matches[1]
$installerName = "Diandian-Setup-$version.exe"
Write-Host "==> 版本号：v$version"

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
if (${env:ProgramFiles(x86)}) {
    $isccCandidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
}
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "未找到 Inno Setup 6（ISCC.exe），请先安装" }

if (-not $SkipBuild) {
    Write-Host "==> 构建绿色版（PyInstaller）..."
    python -m PyInstaller (Join-Path $root "Diandian.spec") --noconfirm `
        --workpath (Join-Path $root "build") --distpath (Join-Path $root "dist")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

    Write-Host "==> 构建安装版（Inno Setup）..."
    # 不吞 ISCC 的编译日志：失败时能看到具体哪一行报错
    & $iscc (Join-Path $root "setup.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 构建失败" }
}

$installerPath = Join-Path $root "installer\$installerName"
if (-not (Test-Path $installerPath)) {
    throw "未找到安装包：$installerPath（新版本需要先完整构建，不能只用 -SkipBuild）"
}

Write-Host "==> 计算安装包校验值..."
$sha256 = (Get-FileHash $installerPath -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $installerPath).Length

if (-not $Notes) {
    # 不传 -Notes 时沿用上一版清单里的说明，避免发版时忘记更新文案
    $previousPath = Join-Path $root "deploy\updates\latest.json"
    if (Test-Path $previousPath) {
        try { $Notes = ((Get-Content $previousPath -Raw -Encoding UTF8) | ConvertFrom-Json).notes } catch { }
    }
}
if (-not $Notes) { $Notes = "修复已知问题，优化使用体验" }
$publishedAt = Get-Date -Format "yyyy-MM-dd"

# ConvertTo-Json 负责全部转义（换行/引号/反斜杠），手拼 here-string 遇到特殊字符会产出非法清单
$manifest = [ordered]@{
    version      = $version
    notes        = $Notes
    pageUrl      = $PageUrl
    installerUrl = "$DownloadBase/$installerName"
    sha256       = $sha256
    size         = $size
    minVersion   = $MinVersion
    publishedAt  = $publishedAt
}
$json = $manifest | ConvertTo-Json

Write-Host ""
Write-Host $json
Write-Host ""

if ($DryRun) {
    Write-Host "==> DryRun 模式：未写入 deploy/updates/latest.json"
    return
}

# 绿色版按版本号另存一份，直接拖进 GitHub Releases 即可
$greenPath = Join-Path $root "dist\Diandian-$version.exe"
Copy-Item (Join-Path $root "dist\点点.exe") $greenPath -Force

# .NET WriteAllText 固定 UTF-8 无 BOM，避免 PowerShell 5.1 的 Out-File 带 BOM 导致 json 解析失败
[System.IO.File]::WriteAllText((Join-Path $root "deploy\updates\latest.json"), $json + [Environment]::NewLine)
Write-Host "==> 已写入 deploy/updates/latest.json"

Write-Host ""
Write-Host "剩余人工步骤："
Write-Host "  1. 上传安装包：$installerPath"
Write-Host "     -> $DownloadBase/$installerName"
Write-Host "  2. 发布 deploy/ 目录，使 $($PageUrl)updates/latest.json 生效"
Write-Host "  3. 上传绿色版到 GitHub Releases：$greenPath（标签 v$version）"
Write-Host "  说明：落地页下载地址自动跟随更新清单，无需修改页面。"
if ($SkipBuild) {
    Write-Host "  注意：本次使用了 -SkipBuild，请确认安装包确实由当前版本代码构建。"
}
