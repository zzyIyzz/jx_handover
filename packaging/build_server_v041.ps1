param(
    [switch]$SkipDependencyInstall,
    [switch]$SkipFrontendBuild,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$releaseDir = Join-Path $projectRoot "release"
$packageName = "江西片区智能交接班_局域网服务器_V0.4.1_win-x64"
$appDir = Join-Path $projectRoot "dist\$packageName"
$zipPath = Join-Path $releaseDir "$packageName.zip"
$shaPath = "$zipPath.sha256"
$releaseManifestPath = "$zipPath.release.json"
$standardTemplateSource = "resources\交接班系统标准导入模板_V0.3.0.xlsx"
$standardTemplateReleaseName = "交接班系统标准导入模板_V0.4.1.xlsx"
$filesToCopy = @(
    "packaging\服务器部署说明_V0.4.1.md",
    "packaging\现场部署测试清单_V0.4.1.md",
    "packaging\升级说明_V0.4.1.md",
    "packaging\安装开机自动启动_系统账户.ps1",
    "packaging\安装登录自动启动_当前账户.ps1",
    "packaging\卸载开机自动启动.ps1"
)

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "未找到项目虚拟环境：$python"
}

Push-Location $projectRoot
try {
    if (-not $SkipDependencyInstall) {
        & $python -m pip install -r "backend\requirements-build.txt"
        if ($LASTEXITCODE -ne 0) { throw "安装构建依赖失败" }
    }

    if (-not $SkipFrontendBuild) {
        Push-Location (Join-Path $projectRoot "frontend")
        try {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw "前端生产构建失败" }
        } finally {
            Pop-Location
        }
    }

    if (-not $SkipTests) {
        $previousPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = Join-Path $projectRoot "backend"
            & $python -m unittest discover -s "backend\tests" -p "test_*.py" -v
            if ($LASTEXITCODE -ne 0) { throw "后端自动化测试失败" }
        } finally {
            $env:PYTHONPATH = $previousPythonPath
        }
        Push-Location (Join-Path $projectRoot "frontend")
        try {
            & npx.cmd vue-tsc --noEmit
            if ($LASTEXITCODE -ne 0) { throw "前端 TypeScript 检查失败" }
        } finally {
            Pop-Location
        }
    }

    & $python "packaging\make_icon.py"
    if ($LASTEXITCODE -ne 0) { throw "生成应用图标失败" }

    & $python -m PyInstaller --noconfirm --clean "packaging\jx_handover_server_v041.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 局域网服务器构建失败" }

    foreach ($relativePath in $filesToCopy) {
        Copy-Item -LiteralPath $relativePath -Destination $appDir -Force
    }
    Copy-Item -LiteralPath $standardTemplateSource `
        -Destination (Join-Path $appDir $standardTemplateReleaseName) -Force

    $releaseInfo = [ordered]@{
        schema_version = 1
        app_id = "jx-handover-server"
        version = "0.4.1"
        channel = "field-test"
        package_directory = $packageName
        port = 8765
        live_database_location = "server-local-fixed-disk"
        nas_role = "verified-backup-and-release-distribution-only"
        data_compatible_from = @("0.3.0", "0.4.0")
        created_at = (Get-Date).ToString("o")
    }
    $releaseInfo | ConvertTo-Json -Depth 6 |
        Set-Content -LiteralPath (Join-Path $appDir "release-info.json") -Encoding utf8

    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -LiteralPath $appDir -DestinationPath $zipPath -CompressionLevel Optimal
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    "$hash  $([IO.Path]::GetFileName($zipPath))" |
        Set-Content -LiteralPath $shaPath -Encoding utf8
    $releaseManifest = [ordered]@{
        schema_version = 1
        app_id = "jx-handover-server"
        version = "0.4.1"
        channel = "field-test"
        package_file = [IO.Path]::GetFileName($zipPath)
        package_size = (Get-Item -LiteralPath $zipPath).Length
        package_sha256 = $hash
        sha256_file = [IO.Path]::GetFileName($shaPath)
        fixed_port = 8765
        requires_local_live_database = $true
        previous_version_rollback_supported = $true
        created_at = (Get-Date).ToString("o")
    }
    $releaseManifest | ConvertTo-Json -Depth 6 |
        Set-Content -LiteralPath $releaseManifestPath -Encoding utf8
    Copy-Item -LiteralPath "packaging\服务器部署说明_V0.4.1.md" -Destination $releaseDir -Force
    Copy-Item -LiteralPath "packaging\现场部署测试清单_V0.4.1.md" -Destination $releaseDir -Force
    Copy-Item -LiteralPath "packaging\升级说明_V0.4.1.md" -Destination $releaseDir -Force
    Copy-Item -LiteralPath $standardTemplateSource `
        -Destination (Join-Path $releaseDir $standardTemplateReleaseName) -Force

    Write-Host "局域网服务器发布包：$zipPath" -ForegroundColor Green
    Write-Host "SHA256：$hash"
    Write-Host "发布清单：$releaseManifestPath"
    Write-Host "请在一台常开 Windows 主机上完整解压，先运行服务器控制器完成配置。"
} finally {
    Pop-Location
}
