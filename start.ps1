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

$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ('bot-{0}.log' -f (Get-Date -Format 'yyyy-MM-dd'))

Write-Host ''
Write-Host 'Dashboard: http://127.0.0.1:5000' -ForegroundColor Green
Write-Host 'Keep this PowerShell window open while automatic posting is enabled.' -ForegroundColor DarkGray
Write-Host "Logging this session to $logFile" -ForegroundColor DarkGray
Write-Host ''

"" | Out-File -FilePath $logFile -Append
"===== Session started $(Get-Date -Format 's') =====" | Out-File -FilePath $logFile -Append

# Flask/Werkzeug write routine startup and request info to the error stream, not just
# real errors. With $ErrorActionPreference = 'Stop', PowerShell treats ANY line python.exe
# sends there (once merged via 2>&1) as fatal and kills the app immediately - so relax
# that just for this one long-running command, or the server dies the instant it prints
# its normal startup banner.
$ErrorActionPreference = 'Continue'
& $pythonExe -u app.py 2>&1 | Tee-Object -FilePath $logFile -Append
$exitCode = $LASTEXITCODE
$ErrorActionPreference = 'Stop'

"===== Session ended $(Get-Date -Format 's') (exit code $exitCode) =====" | Out-File -FilePath $logFile -Append

Write-Host ''
if ($exitCode -ne 0) {
    Write-Host "app.py stopped on its own with an error (exit code $exitCode)." -ForegroundColor Red
} else {
    Write-Host 'app.py stopped.' -ForegroundColor Yellow
}
Write-Host "Full output from this run was saved to: $logFile" -ForegroundColor DarkGray
Read-Host 'Press Enter to close this window'
