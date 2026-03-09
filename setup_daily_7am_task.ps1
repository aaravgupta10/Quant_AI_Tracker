$ErrorActionPreference = "Stop"

$taskName = "Nifty500DailyMarketEmail"
$projectPath = $PSScriptRoot
$scriptPath = Join-Path $projectPath "run_daily_market_report.ps1"

if (-not (Test-Path $scriptPath)) {
    throw "Script not found: $scriptPath"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Send Nifty 500 daily market report email at 7AM" -Force | Out-Null
    Write-Host "Scheduled task '$taskName' created/updated for 7:00 AM daily."
} catch {
    Write-Error "Failed to create scheduled task. Run this PowerShell script as Administrator if needed. Error: $($_.Exception.Message)"
    exit 1
}
