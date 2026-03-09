$ErrorActionPreference = "Stop"
Set-Location -Path "$PSScriptRoot"

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python executable not found at $python"
}

& $python ".\daily_market_intelligence.py" --email-to "aaravgupta1009@gmail.com"
