param(
    [string]$ZipPath = "",
    [string]$WorkbookPath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $ZipPath) {
    $ZipPath = Join-Path $repoRoot "release\江西片区智能交接班_局域网服务器_V0.4.0_win-x64.zip"
}
if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "找不到服务端发布包：$ZipPath"
}
if (-not $WorkbookPath) {
    throw "请使用 -WorkbookPath 传入仅用于本机验收的实际工作日志 XLSX；脚本不会把该文件复制到仓库。"
}
if (-not (Test-Path -LiteralPath $WorkbookPath -PathType Leaf)) {
    throw "找不到验收工作日志 Excel：$WorkbookPath"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$smokeRoot = Join-Path $env:TEMP ("JXV040-Final-Packaged-独立冒烟-" + $stamp)
$extractRoot = Join-Path $smokeRoot "中文 路径 最终包"
$packageName = "江西片区智能交接班_局域网服务器_V0.4.0_win-x64"
$package = Join-Path $extractRoot $packageName
$controlRoot = Join-Path $smokeRoot "服务器控制配置"
$dataRoot = Join-Path $smokeRoot "正式数据 本地固定盘"
$nasRoot = Join-Path $smokeRoot "模拟云盘 NAS"
$serverProcess = $null
$serverProcess2 = $null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Invoke-ApiJson([string]$Method, [string]$Path, $Body = $null) {
    $params = @{
        Uri = ("http://127.0.0.1:8765/api" + $Path)
        Method = $Method
        TimeoutSec = 30
    }
    if ($null -ne $Body) {
        $params["Body"] = ($Body | ConvertTo-Json -Depth 12)
        $params["ContentType"] = "application/json"
    }
    return Invoke-RestMethod @params
}

function Wait-Health {
    param([int]$ProcessId)
    $health = $null
    for ($attempt = 1; $attempt -le 80; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/health" -Method Get -TimeoutSec 2
            if ($health.status -eq "ok") { return $health }
        } catch {
            if ($ProcessId -gt 0) {
                try {
                    $running = Get-Process -Id $ProcessId -ErrorAction Stop
                    if ($running.HasExited) { throw "服务器进程提前退出，PID=$ProcessId" }
                } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
                    throw "服务器进程提前退出，PID=$ProcessId"
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "最终 EXE 35 秒内未通过健康检查"
}

function Stop-Gracefully {
    param($ProcessObject, [string]$ControlDirectory)
    if ($null -eq $ProcessObject) { return }
    $pidPath = Join-Path $ControlDirectory "control\server.pid"
    $stopPath = Join-Path $ControlDirectory "control\stop.request"
    if (Test-Path -LiteralPath $pidPath) {
        $pidState = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
        $payload = [ordered]@{
            instance_id = [string]$pidState.instance_id
            requested_at = (Get-Date).ToString("o")
            requested_by = "packaged-independent-smoke"
            action = "graceful-stop"
        }
        [IO.Directory]::CreateDirectory((Split-Path -Parent $stopPath)) | Out-Null
        [IO.File]::WriteAllText($stopPath, ($payload | ConvertTo-Json -Depth 5), $utf8NoBom)
    }
    for ($attempt = 1; $attempt -le 70; $attempt++) {
        if ($ProcessObject.HasExited) { break }
        Start-Sleep -Milliseconds 500
        $ProcessObject.Refresh()
    }
    if (-not $ProcessObject.HasExited) {
        throw "服务端优雅停止未在 35 秒内完成；未强制结束进程。"
    }
    if (Test-Path -LiteralPath $pidPath) {
        throw "优雅停止后 PID 文件仍残留：$pidPath"
    }
}

try {
    New-Item -ItemType Directory -Path $extractRoot, $controlRoot, $dataRoot, $nasRoot -Force | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $extractRoot -Force
    if (-not (Test-Path -LiteralPath (Join-Path $package "交接班服务器.exe") -PathType Leaf)) {
        throw "解压后的服务器 EXE 不存在"
    }

    $settings = [ordered]@{
        public_host = "127.0.0.1"
        data_root = $dataRoot
        qwen_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        qwen_model = "qwen3.8-flash"
        admin_names = ""
        nas_backup_dir = $nasRoot
        auto_open_browser = $false
        auth_required = $false
    }
    [IO.File]::WriteAllText(
        (Join-Path $controlRoot "server-settings.json"),
        ($settings | ConvertTo-Json -Depth 8),
        $utf8NoBom
    )

    $env:JX_HANDOVER_SERVER_HOME = $controlRoot
    $serverExe = Join-Path $package "交接班服务器.exe"
    $serverProcess = Start-Process -FilePath $serverExe -WorkingDirectory $package -PassThru -WindowStyle Hidden
    $health = Wait-Health -ProcessId $serverProcess.Id
    if ($health.version -ne "0.4.0" -or $health.mode -ne "server" -or [int]$health.port -ne 8765) {
        throw ("健康信息不符合预期：" + ($health | ConvertTo-Json -Compress))
    }
    if ($health.public_url -ne "http://127.0.0.1:8765") {
        throw "自定义访问地址未生效：$($health.public_url)"
    }
    $processInfo = Get-Process -Id $serverProcess.Id
    if ($processInfo.MainWindowHandle -ne 0 -or $processInfo.MainWindowTitle) {
        throw "后台服务器出现可见窗口"
    }

    $stations = Invoke-ApiJson "GET" "/stations"
    $station = @($stations | Where-Object { $_.code -eq "XS_MMS" })[0]
    if ($null -eq $station) { throw "没有找到 XS_MMS 测试场站" }
    $overrides = @{}
    $overrides[[string]$station.id] = @{
        duty_leader = "测试负责人"
        temp_leader = "无"
        operators = @("测试值班员")
    }
    $batch = Invoke-ApiJson "POST" "/handovers" @{
        start_date = "2026-08-14"
        end_date = "2026-08-23"
        handover_date = "2026-08-23"
        station_ids = @([int]$station.id)
        meta_overrides = $overrides
    }
    $batchId = [string]$batch.id
    $detail = Invoke-ApiJson "GET" ("/handovers/" + $batchId)
    $stationDetail = @($detail.stations)[0]
    $metaId = [string]$stationDetail.station_meta_id
    $beforeItems = @($stationDetail.items).Count

    Add-Type -AssemblyName System.Net.Http
    $client = [System.Net.Http.HttpClient]::new()
    $fileStream = [IO.File]::OpenRead($WorkbookPath)
    try {
        $multipart = [System.Net.Http.MultipartFormDataContent]::new()
        $filePart = [System.Net.Http.StreamContent]::new($fileStream)
        $filePart.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        $multipart.Add($filePart, "file", "acceptance.xlsx")
        $stationPart = [System.Net.Http.StringContent]::new($metaId)
        $multipart.Add($stationPart, "station_meta_id")
        $response = $client.PostAsync(
            ("http://127.0.0.1:8765/api/handovers/" + $batchId + "/imports/preview"),
            $multipart
        ).Result
        [void]$response.EnsureSuccessStatusCode()
        $preview = $response.Content.ReadAsStringAsync().Result | ConvertFrom-Json
    } finally {
        $fileStream.Dispose()
        $client.Dispose()
    }
    if ($preview.parser_key -ne "work_log") { throw "实际工作簿解析器错误：$($preview.parser_key)" }
    $previewRows = @($preview.rows)
    if ($previewRows.Count -lt 1) { throw "实际工作簿预览没有有效行" }
    $formalBeforeCommit = @((Invoke-ApiJson "GET" ("/handovers/" + $batchId)).stations[0].items).Count
    if ($formalBeforeCommit -ne $beforeItems) { throw "预览阶段改动了正式事项" }
    Invoke-ApiJson "POST" ("/handovers/" + $batchId + "/imports/" + [string]$preview.id + "/commit") @{ rows = $previewRows } | Out-Null
    $afterDetail = Invoke-ApiJson "GET" ("/handovers/" + $batchId)
    $afterItems = @($afterDetail.stations[0].items)
    if ($afterItems.Count -le $beforeItems) { throw "确认导入后正式事项没有增加" }

    $render = Invoke-ApiJson "POST" ("/handovers/" + $batchId + "/render") @{ station_meta_id = $metaId }
    $docxPath = [string]$render.docx_path
    if (-not (Test-Path -LiteralPath $docxPath -PathType Leaf)) { throw "最终 EXE 生成的 Word 不存在：$docxPath" }
    if ((Get-Item -LiteralPath $docxPath).Length -lt 10000) { throw "最终 EXE 生成的 Word 文件过小" }

    $backup = Invoke-ApiJson "POST" "/admin/backup"
    if (-not $backup.nas_path) { throw "NAS 模拟备份没有返回共享路径" }
    if (-not (Test-Path -LiteralPath ([string]$backup.nas_path) -PathType Leaf)) { throw "NAS 模拟备份文件不存在" }
    $localSha = (Get-FileHash -LiteralPath ([string]$backup.local_path) -Algorithm SHA256).Hash
    $nasSha = (Get-FileHash -LiteralPath ([string]$backup.nas_path) -Algorithm SHA256).Hash
    if ($localSha -ne $nasSha) { throw "NAS 模拟备份 SHA256 不一致" }
    if (-not (Test-Path -LiteralPath (Join-Path $dataRoot "data\handover.db") -PathType Leaf)) {
        throw "自定义正式数据目录未产生 SQLite 数据库"
    }

    Stop-Gracefully -ProcessObject $serverProcess -ControlDirectory $controlRoot
    $serverProcess = $null
    $env:JX_HANDOVER_SERVER_HOME = $controlRoot
    $serverProcess2 = Start-Process -FilePath $serverExe -WorkingDirectory $package -PassThru -WindowStyle Hidden
    $health2 = Wait-Health -ProcessId $serverProcess2.Id
    $persisted = Invoke-ApiJson "GET" ("/handovers/" + $batchId)
    if ($persisted.id -ne $batchId -or @($persisted.stations[0].items).Count -ne $afterItems.Count) {
        throw "重启后班次或事项没有保留"
    }
    Stop-Gracefully -ProcessObject $serverProcess2 -ControlDirectory $controlRoot
    $serverProcess2 = $null

    [pscustomobject]@{
        SmokeRoot = $smokeRoot
        Package = $package
        ControlRoot = $controlRoot
        DataRoot = $dataRoot
        NasRoot = $nasRoot
        HealthVersion = $health.version
        HealthMode = $health.mode
        HealthPort = $health.port
        PreviewParser = $preview.parser_key
        PreviewRows = $previewRows.Count
        FormalItemsBeforePreview = $formalBeforeCommit
        FormalItemsAfterCommit = $afterItems.Count
        RenderedWord = $docxPath
        BackupLocal = $backup.local_path
        BackupNas = $backup.nas_path
        BackupSHA256 = $localSha
        RestartPreserved = $true
        HiddenServerWindow = ($processInfo.MainWindowHandle -eq 0 -and [string]::IsNullOrEmpty($processInfo.MainWindowTitle))
    } | Format-List
} finally {
    if ($null -ne $serverProcess) { Stop-Gracefully -ProcessObject $serverProcess -ControlDirectory $controlRoot }
    if ($null -ne $serverProcess2) { Stop-Gracefully -ProcessObject $serverProcess2 -ControlDirectory $controlRoot }
}
