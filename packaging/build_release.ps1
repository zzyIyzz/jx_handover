param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$releaseDir = Join-Path $projectRoot "release"

# The package is built from whatever the source tree currently holds, so its name
# has to come from the same VERSION file the application reports at runtime.
# Naming today's build after an older release would hand the field an EXE whose
# label contradicts its own management page.
$versionFile = Join-Path $projectRoot "VERSION"
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
    throw "缺少版本文件：$versionFile"
}
$appVersion = ((Get-Content -LiteralPath $versionFile -Raw) -split "`n")[0].Trim()
if ($appVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION 文件内容不是 x.y.z 格式：$appVersion"
}
$versionTag = "V$appVersion"

$appDir = Join-Path $projectRoot "dist\江西片区智能交接班_${versionTag}_win-x64"
$zipPath = Join-Path $releaseDir "江西片区智能交接班_${versionTag}_win-x64.zip"
$shaPath = "$zipPath.sha256"
$standardTemplateSource = "resources\交接班系统标准导入模板_V0.3.0.xlsx"
$standardTemplateReleaseName = "交接班系统标准导入模板_${versionTag}.xlsx"
$upgradeNote = Join-Path $projectRoot "packaging\升级说明_${versionTag}.md"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目虚拟环境：$python"
}

Push-Location $projectRoot
try {
    if (-not $SkipDependencyInstall) {
        & $python -m pip install -r "backend\requirements-build.txt"
        if ($LASTEXITCODE -ne 0) { throw "安装构建依赖失败" }
    }

    Push-Location (Join-Path $projectRoot "frontend")
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "前端生产构建失败" }
    } finally {
        Pop-Location
    }

    & $python "packaging\make_icon.py"
    if ($LASTEXITCODE -ne 0) { throw "生成应用图标失败" }

    & $python "packaging\make_version_info.py"
    if ($LASTEXITCODE -ne 0) { throw "生成 EXE 版本资源失败" }

    & $python -m PyInstaller --noconfirm --clean "packaging\jx_handover.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

    if (Test-Path -LiteralPath $upgradeNote -PathType Leaf) {
        Copy-Item -LiteralPath $upgradeNote -Destination $appDir -Force
    } else {
        Write-Warning "未找到 $versionTag 的升级说明，发布包内将不包含升级文档。"
    }
    Copy-Item -LiteralPath $standardTemplateSource `
        -Destination (Join-Path $appDir $standardTemplateReleaseName) -Force

    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $appDir -DestinationPath $zipPath -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    "$hash  $([IO.Path]::GetFileName($zipPath))" | Set-Content -LiteralPath $shaPath -Encoding ascii
    Copy-Item -LiteralPath $standardTemplateSource `
        -Destination (Join-Path $releaseDir $standardTemplateReleaseName) -Force
    if (Test-Path -LiteralPath $upgradeNote -PathType Leaf) {
        Copy-Item -LiteralPath $upgradeNote -Destination $releaseDir -Force
    }

    Write-Host "发布包：$zipPath"
    Write-Host "版本：$appVersion"
    Write-Host "SHA256：$hash"
} finally {
    Pop-Location
}
