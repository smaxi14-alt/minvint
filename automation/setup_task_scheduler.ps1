# 쓰레드 자동 발행 — Windows 작업 스케줄러 등록 스크립트
# 관리자 권한 없이도 현재 사용자 계정으로 등록 가능
# 실행: PowerShell에서 .\setup_task_scheduler.ps1

$PythonExe  = "C:\Users\ant_6\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPath = "C:\Users\ant_6\OneDrive\문서\마케팅\automation\post_once.py"
$WorkDir    = "C:\Users\ant_6\OneDrive\문서\마케팅\automation"
$LogDir     = "$WorkDir\logs"

$slots = @(
    @{ Name = "Threads-Morning"; Slot = "morning"; Hour = 7;  Minute = 30 },
    @{ Name = "Threads-Noon";    Slot = "noon";    Hour = 12; Minute = 30 },
    @{ Name = "Threads-Evening"; Slot = "evening"; Hour = 21; Minute = 0  }
)

foreach ($s in $slots) {
    $taskName = $s.Name
    $logFile  = "$LogDir\$($s.Slot).log"

    # 기존 작업이 있으면 삭제 후 재등록
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "기존 작업 제거: $taskName"
    }

    $action  = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument "$ScriptPath --slot $($s.Slot) >> `"$logFile`" 2>&1" `
        -WorkingDirectory $WorkDir

    $trigger = New-ScheduledTaskTrigger `
        -Daily `
        -At "$($s.Hour):$($s.Minute.ToString('D2'))"

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -RestartCount 2 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -StartWhenAvailable   # PC가 꺼져 있었으면 켜진 후 즉시 실행

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action   $action `
        -Trigger  $trigger `
        -Settings $settings `
        -RunLevel Limited `
        -Force | Out-Null

    Write-Host "등록 완료: $taskName  ($($s.Hour):$($s.Minute.ToString('D2')) KST)"
}

Write-Host ""
Write-Host "확인: Get-ScheduledTask -TaskName 'Threads-*' | Select-Object TaskName, State"
Get-ScheduledTask -TaskName "Threads-*" | Select-Object TaskName, State
