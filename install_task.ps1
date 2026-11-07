# Đăng ký Windows Scheduled Task để tự chạy khi đăng nhập.
# Chạy: powershell -ExecutionPolicy Bypass -File .\install_task.ps1
$ErrorActionPreference = "Stop"

$taskName = "UIT Portal Checker"
$projectDir = $PSScriptRoot
$runner = Join-Path $projectDir "run.ps1"

if (-not (Test-Path $runner)) {
    throw "Không tìm thấy $runner"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" `
    -WorkingDirectory $projectDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Theo dõi TKB, lịch thi và thông báo UIT; gửi Telegram." `
    -Force | Out-Null

Write-Host "Đã đăng ký task '$taskName'."
Write-Host "Task sẽ chạy khi user đăng nhập Windows."
Write-Host "Kiểm tra: Get-ScheduledTask -TaskName '$taskName'"
