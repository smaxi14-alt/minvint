# 셀럽 트렌드 블로그 — 감시(watchdog) 스케줄러 등록 스크립트
# 실행: PowerShell에서 .\setup_celeb_watchdog_scheduler.ps1
#
# 배경(2026-07-16): CelebBlog-Morning(10:00)과 CelebBlog-Afternoon(14:00)이
# 같은 날 두 번이나 STATUS_CONTROL_C_EXIT(3221225786)로 조용히 중단됐다 —
# 보험 블로그 watchdog 조사 때 발견한 것과 동일한 패턴(automation 폴더가
# OneDrive 동기화 대상 ReparsePoint라 작업 스케줄러 실행 순간 경합 추정,
# 100% 확정은 아님). 셀럽 블로그는 하루 최대 3건(10/14/19시 슬롯)까지
# 발행하므로, "오늘 발행됐는가"가 아니라 "지금 시각 기준 있어야 할 건수만큼
# 있는가"로 판단하는 celeb_daily_watchdog.py를 만들었다. 이 스크립트는 그
# 감시 스크립트를 각 슬롯 이후 일정 시간 지난 시각에 실행되도록 등록한다.
# 이미 따라잡았거나 원래 실행이 아직 진행 중이면(동시실행 방지 잠금,
# run_celeb_pipeline.py의 _acquire_lock 참조) 아무 일도 안 하고 조용히 끝난다.

$PythonExe  = "C:\Users\ant_6\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPath = "C:\Users\ant_6\OneDrive\문서\마케팅\automation\celeb_daily_watchdog.py"
$WorkDir    = "C:\Users\ant_6\OneDrive\문서\마케팅\automation"
$LogDir     = "$WorkDir\logs"
$LogFile    = "$LogDir\celeb_daily_watchdog.log"

$taskName = "CelebBlog-Watchdog"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "기존 작업 제거: $taskName"
}

# 보험 블로그 watchdog과 동일한 이유로 cmd.exe /c로 감싼다 — Execute에
# python.exe를 직접 지정하고 Argument에 셸 리다이렉션(">>"/"2>&1")을 그대로
# 넣으면 CreateProcess가 셸을 거치지 않아 리다이렉션 문자가 파이썬에 그대로
# 전달되고 argparse가 거부해 조용히 실패한다(2026-07-15 실측).
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogFile`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# 각 슬롯(10/14/19시) 25분 후 확인 — 정상 실행 소요시간(관측상 5~40분) 중
# 대부분을 커버하면서도 다음 슬롯과는 안 겹치게 간격을 둔다.
$trigger1 = New-ScheduledTaskTrigger -Daily -At "10:25"
$trigger2 = New-ScheduledTaskTrigger -Daily -At "14:25"
$trigger3 = New-ScheduledTaskTrigger -Daily -At "19:25"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  @($trigger1, $trigger2, $trigger3) `
    -Settings $settings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "등록 완료: $taskName (매일 10:25 / 14:25 / 19:25 — 슬롯별 미달 시에만 재시도, 하루 최대 3회)"
Write-Host ""
Write-Host "확인: Get-ScheduledTask -TaskName 'CelebBlog-Watchdog' | Select-Object TaskName, State"
Write-Host "수동 1회 실행(테스트): Start-ScheduledTask -TaskName 'CelebBlog-Watchdog'"
Write-Host "중지/비활성화: Disable-ScheduledTask -TaskName 'CelebBlog-Watchdog'"
Write-Host "완전 삭제: Unregister-ScheduledTask -TaskName 'CelebBlog-Watchdog' -Confirm:`$false"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
