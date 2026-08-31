param(
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$taskName = "江西片区智能交接班服务器"
$firewallName = "江西片区智能交接班服务器 TCP 8765"
$serverExe = Join-Path $PSScriptRoot "交接班服务器.exe"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请右键此脚本并选择‘使用 PowerShell 运行’，在 UAC 提示中允许管理员权限。"
}
if (-not (Test-Path -LiteralPath $serverExe -PathType Leaf)) {
    throw "未找到服务器程序：$serverExe。请在完整解压后的发布目录内运行本脚本。"
}

# 两种自启动方式互斥，避免升级后留下两个指向不同版本目录的任务。
$interactiveTask = "江西片区智能交接班服务器-当前账户"
if (Get-ScheduledTask -TaskName $interactiveTask -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $interactiveTask -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $serverExe -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$systemPrincipal = New-ScheduledTaskPrincipal `
    -UserId "S-1-5-18" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $systemPrincipal `
    -Settings $settings `
    -Description "开机自动运行江西片区智能交接班 V0.4.0 局域网服务器" `
    -Force | Out-Null

if (-not $SkipFirewall) {
    $existing = Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue
    if ($existing) {
        $existing | Remove-NetFirewallRule
    }
    New-NetFirewallRule `
        -DisplayName $firewallName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8765 `
        -Profile Domain,Private `
        -Program $serverExe `
        -Description "仅允许域网络和专用局域网访问交接班服务器" | Out-Null
}

Start-ScheduledTask -TaskName $taskName
Write-Host "已安装并启动计划任务：$taskName" -ForegroundColor Green
Write-Host "已配置为 SYSTEM 开机自启；控制器可以随时关闭。"
Write-Warning "SYSTEM 默认没有远程 NAS 的用户凭据。NAS 备份失败时，本地备份仍会保留；请参阅服务器部署说明。"
Read-Host "按 Enter 键关闭"
