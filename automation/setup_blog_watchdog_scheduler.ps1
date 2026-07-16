# 네이버 블로그 1일 1글 — 감시(watchdog) 스케줄러 등록 스크립트
# 실행: PowerShell에서 .\setup_blog_watchdog_scheduler.ps1
#
# 배경(2026-07-16): 11:00 본 작업(NaverBlog-Daily)이 로그 한 줄 남기지 못하고
# exit code 1로 조용히 실패한 사고가 있었다. 이후 이 감시 작업 자체도 첫 실행
# (12:30)에서 Win32 오류 267("디렉터리 이름이 올바르지 않습니다")로 실패해
# 원인에 좀 더 다가섰다 — automation 폴더가 OneDrive 동기화 대상(ReparsePoint
# 속성 확인됨)이라, 작업 스케줄러가 프로세스를 띄우는 순간 OneDrive 동기화
# 엔진과 경합해 경로 해석이 순간 실패하는 것으로 추정된다(100% 확정은 아님).
# 근본 원인을 코드로 직접 막을 수 없으니, "오늘 발행됐는가"를 결과 기준으로
# 감시해 재시도하는 blog_daily_watchdog.py를 만들었다. 이 스크립트는 그
# 감시 스크립트를 본 작업(07:00, 2026-07-16 부로 11:00에서 변경) 이후 하루 중
# 몇 차례 더 실행되도록 등록한다. 이미 발행된 날은 매번 즉시 종료하므로
# 정상적인 날엔 아무 영향이 없다.

$PythonExe  = "C:\Users\ant_6\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPath = "C:\Users\ant_6\OneDrive\문서\마케팅\automation\blog_daily_watchdog.py"
$WorkDir    = "C:\Users\ant_6\OneDrive\문서\마케팅\automation"
$LogDir     = "$WorkDir\logs"
$LogFile    = "$LogDir\blog_daily_watchdog.log"

$taskName = "NaverBlog-DailyWatchdog"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "기존 작업 제거: $taskName"
}

# setup_blog_scheduler.ps1과 동일한 이유로 cmd.exe /c로 감싼다 — Execute에
# python.exe를 직접 지정하고 Argument에 셸 리다이렉션(">>"/"2>&1")을 그대로
# 넣으면 CreateProcess가 셸을 거치지 않아 리다이렉션 문자가 파이썬에 그대로
# 전달되고 argparse가 거부해 조용히 실패한다(2026-07-15 실측, run_daily_blog.py
# 쪽에서 먼저 발견됨).
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogFile`" 2>&1`"" `
    -WorkingDirectory $WorkDir

# 07:00 본 작업 이후 하루 중 세 번 더 확인 — Gemini 이미지 API 장애 같은
# 일시적 외부 문제가 몇 시간 안에 풀리는 경우까지 커버하기 위해 간격을 둔다.
$trigger1 = New-ScheduledTaskTrigger -Daily -At "08:30"
$trigger2 = New-ScheduledTaskTrigger -Daily -At "11:00"
$trigger3 = New-ScheduledTaskTrigger -Daily -At "13:30"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  @($trigger1, $trigger2, $trigger3) `
    -Settings $settings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "등록 완료: $taskName (매일 08:30 / 11:00 / 13:30 — 미발행 시에만 재시도, 하루 최대 3회)"
Write-Host ""
Write-Host "확인: Get-ScheduledTask -TaskName 'NaverBlog-DailyWatchdog' | Select-Object TaskName, State"
Write-Host "수동 1회 실행(테스트): Start-ScheduledTask -TaskName 'NaverBlog-DailyWatchdog'"
Write-Host "중지/비활성화: Disable-ScheduledTask -TaskName 'NaverBlog-DailyWatchdog'"
Write-Host "완전 삭제: Unregister-ScheduledTask -TaskName 'NaverBlog-DailyWatchdog' -Confirm:`$false"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
