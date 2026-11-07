# Gỡ Windows Scheduled Task.
$ErrorActionPreference = "Stop"
$taskName = "UIT Portal Checker"
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Host "Đã gỡ task '$taskName'."
