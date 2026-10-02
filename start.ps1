$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pyLauncher = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the Python environment. Install Python 3.10 or newer, then run this file again.'
    }
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Host 'Created .env from .env.example.' -ForegroundColor Cyan
}

& $pythonExe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw 'Could not install dependencies. Check your internet connection and try again.'
}

Write-Host ''
Write-Host 'Dashboard: http://127.0.0.1:5000   Health: http://127.0.0.1:5000/health' -ForegroundColor Green
Write-Host 'Keep this PowerShell window open (or run install_autostart.ps1 to run it in the background at sign-in).' -ForegroundColor DarkGray
Write-Host 'The bot restarts itself automatically if it stops. Logs are in the logs folder.' -ForegroundColor DarkGray
Write-Host ''

& (Join-Path $PSScriptRoot 'run_bot.ps1')
Read-Host 'Press Enter to close this window'
