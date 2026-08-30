param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$releaseDir = Join-Path $projectRoot "release"
$appDir = Join-Path $projectRoot "dist\江西片区智能交接班_V0.3.0_win-x64"
$zipPath = Join-Path $releaseDir "江西片区智能交接班_V0.3.0_win-x64.zip"
$shaPath = "$zipPath.sha256"

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

    & $python -m PyInstaller --noconfirm --clean "packaging\jx_handover.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

    Copy-Item -LiteralPath "packaging\升级说明_V0.3.0.md" -Destination $appDir -Force
    Copy-Item -LiteralPath "resources\交接班系统标准导入模板_V0.3.0.xlsx" -Destination $appDir -Force

    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $appDir -DestinationPath $zipPath -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    "$hash  $([IO.Path]::GetFileName($zipPath))" | Set-Content -LiteralPath $shaPath -Encoding ascii
    Copy-Item -LiteralPath "resources\交接班系统标准导入模板_V0.3.0.xlsx" -Destination $releaseDir -Force
    Copy-Item -LiteralPath "packaging\升级说明_V0.3.0.md" -Destination $releaseDir -Force

    Write-Host "发布包：$zipPath"
    Write-Host "SHA256：$hash"
} finally {
    Pop-Location
}
