param(
    [switch]$RemoveFirewall
)

$ErrorActionPreference = "Stop"
$taskNames = @(
    "江西片区智能交接班服务器",
    "江西片区智能交接班服务器-当前账户"
)
$firewallName = "江西片区智能交接班服务器 TCP 8765"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请在管理员 PowerShell 中运行此脚本。"
}

foreach ($taskName in $taskNames) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "已删除计划任务：$taskName" -ForegroundColor Green
    }
}
if ($RemoveFirewall) {
    Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
    Write-Host "已删除防火墙规则：$firewallName"
} else {
    Write-Host "防火墙规则已保留。如需一并移除，请用 -RemoveFirewall 参数运行本脚本。"
}
Write-Host "数据库、服务器设置、Key、日志和备份均未删除。"
Read-Host "按 Enter 键关闭"
