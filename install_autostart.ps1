# Registers a Windows scheduled task that starts the bot hidden whenever you sign in,
# so it keeps posting after reboots without a PowerShell window open.
#   Install:   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
#   Remove:    powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Uninstall
#   Run several bots? Give each copy its own -TaskName (and APP_PORT in its .env).
param([switch]$Uninstall, [string]$TaskName = 'Bluesky News Feed Bot')

$ErrorActionPreference = 'Stop'

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the '$taskName' startup task." -ForegroundColor Yellow
    return
}

$script = Join-Path $PSScriptRoot 'run_bot.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Installed '$taskName': the bot now starts automatically when you sign in." -ForegroundColor Green
Write-Host "Start it now without signing out:  Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor DarkGray
