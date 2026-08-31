param(
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$taskName = "江西片区智能交接班服务器-当前账户"
$firewallName = "江西片区智能交接班服务器 TCP 8765"
$serverExe = Join-Path $PSScriptRoot "交接班服务器.exe"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请在管理员 PowerShell 中运行此脚本。"
}
if (-not (Test-Path -LiteralPath $serverExe -PathType Leaf)) {
    throw "未找到服务器程序：$serverExe。"
}

# 两种自启动方式互斥，避免同一台服务器同时保留两个版本的运行任务。
$systemTask = "江西片区智能交接班服务器"
if (Get-ScheduledTask -TaskName $systemTask -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $systemTask -Confirm:$false
}

$userId = $identity.Name
$action = New-ScheduledTaskAction -Execute $serverExe -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$userPrincipal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
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
    -Principal $userPrincipal `
    -Settings $settings `
    -Description "用户登录后运行江西片区智能交接班 V0.4.0 局域网服务器" `
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

Write-Host "已安装当前账户登录自启：$taskName" -ForegroundColor Green
Write-Warning "此方案只有该账户登录后才会运行，不适合无人值守服务器；但可使用该账户已有的 NAS 凭据。"
Read-Host "按 Enter 键关闭"
