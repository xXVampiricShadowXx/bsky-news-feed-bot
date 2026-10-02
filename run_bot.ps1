# Runs the bot and restarts it if it ever exits (crash, hang detected by its own
# watchdog, network stack trouble, etc.). Used by start.ps1 and the logon task
# created by install_autostart.ps1. Needs the .venv that start.ps1 creates.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Python environment not found. Run start.ps1 once first.'
}

$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Keep two weeks of daily logs.
Get-ChildItem -LiteralPath $logDir -Filter 'bot-*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-14) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

$alreadyRunningExitCode = 10
$configErrorExitCode = 2
$delaySeconds = 5

while ($true) {
    $logFile = Join-Path $logDir ('bot-{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))
    "===== Session started $(Get-Date -Format 's') =====" | Out-File -FilePath $logFile -Append
    $started = Get-Date

    # Python logs routine output to stderr; don't let PowerShell treat that as fatal.
    $ErrorActionPreference = 'Continue'
    & $pythonExe -u app.py 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $logFile -Append
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'

    "===== Session ended $(Get-Date -Format 's') (exit code $exitCode) =====" | Out-File -FilePath $logFile -Append

    if ($exitCode -eq $alreadyRunningExitCode) {
        Write-Host 'Another copy of the bot is already running; not starting a second one.' -ForegroundColor Yellow
        break
    }
    if ($exitCode -eq $configErrorExitCode) {
        Write-Host 'The bot stopped because of a settings problem (see the message above). Fix .env and start again.' -ForegroundColor Red
        break
    }

    # Back off if it keeps dying quickly; reset after a run that lasted a while.
    if (((Get-Date) - $started).TotalMinutes -ge 10) { $delaySeconds = 5 }
    Write-Host "Bot exited (code $exitCode). Restarting in $delaySeconds seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds $delaySeconds
    $delaySeconds = [Math]::Min($delaySeconds * 2, 300)
}
