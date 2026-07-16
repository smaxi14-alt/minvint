# 네이버 블로그 1일 1글 자동 발행 — Windows 작업 스케줄러 등록 스크립트
# 관리자 권한 없이도 현재 사용자 계정으로 등록 가능
# 실행: PowerShell에서 .\setup_blog_scheduler.ps1
#
# 주의: 이 작업은 매일 07:00 KST에 run_daily_blog.py를 실행한다(2026-07-16,
# 기존 11:00에서 변경 — Gemini 이미지 생성 API의 503/504 과부하 오류가 미국
# 업무시간대와 겹칠 가능성이 있는 낮 시간대보다, 상대적으로 한산할 것으로
# 추정되는 이른 아침 시간대를 노려본 조정. 통계적으로 확정된 패턴은 아니고
# 합리적 추정에 근거한 시도임).
# run_daily_blog.py는 네이버 발행 단계에서 실제 브라우저 창을 띄우고
# pyautogui로 마우스/키보드를 직접 조작한다(automation/naver_blog_poster.py
# 참조) — 그 시각에 PC 화면이 잠겨 있으면 이미지 업로드 단계가 실패할 수
# 있으니, 07시경에는 PC 화면 잠금을 걸지 않는 것을 권장한다
# (_context/naver-blog-6month-strategy.md 운영 리스크 섹션 참조).

$PythonExe  = "C:\Users\ant_6\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPath = "C:\Users\ant_6\OneDrive\문서\마케팅\automation\run_daily_blog.py"
$WorkDir    = "C:\Users\ant_6\OneDrive\문서\마케팅\automation"
$LogDir     = "$WorkDir\logs"
$LogFile    = "$LogDir\blog_daily.log"

$taskName = "NaverBlog-Daily"

# 기존 작업이 있으면 삭제 후 재등록
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "기존 작업 제거: $taskName"
}

# 주의(2026-07-15 실측 발견): Windows 작업 스케줄러의 "프로그램 시작" 액션은
# 셸을 거치지 않고 대상 실행 파일을 직접 호출한다(CreateProcess) — 그래서
# Execute에 python.exe를 직접 지정하고 Argument에 ">>"/"2>&1" 같은 셸 리다이렉션
# 문법을 넣으면, 셸이 해석해주지 않고 그 문자들이 그대로 python.exe의 명령줄
# 인자로 전달된다. argparse가 이를 "unrecognized arguments"로 거부하며 즉시
# exit code 2로 종료 — 실제 스크립트 로직은 단 한 줄도 실행되지 않고, 로그
# 파일도 전혀 생성되지 않는다(리다이렉션 자체가 발동한 적이 없으므로). 이
# 상태로 등록된 스케줄러는 트리거는 되지만 매번 이 실패로 조용히 끝난다 —
# 수동으로 python 스크립트를 직접 실행해서 검증했을 때는 셸(Bash/PowerShell)을
# 거치므로 리다이렉션이 정상 작동해 이 문제가 드러나지 않았다. 실제 스케줄러
# 트리거 경로(Start-ScheduledTask)로 재현·확인 완료. 해결: cmd.exe /c로
# 감싸서 실제 셸을 거치게 하면 리다이렉션이 정상 해석된다.
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$LogFile`" 2>&1`"" `
    -WorkingDirectory $WorkDir

$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "07:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable   # PC가 꺼져 있었으면 켜진 후 즉시 실행 (단, pyautogui 단계는 화면 잠금 해제 상태여야 성공)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  $trigger `
    -Settings $settings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "등록 완료: $taskName  (매일 07:00 KST)"
Write-Host ""
Write-Host "확인: Get-ScheduledTask -TaskName 'NaverBlog-Daily' | Select-Object TaskName, State"
Write-Host "수동 1회 실행(테스트): Start-ScheduledTask -TaskName 'NaverBlog-Daily'"
Write-Host "중지/비활성화: Disable-ScheduledTask -TaskName 'NaverBlog-Daily'"
Write-Host "완전 삭제: Unregister-ScheduledTask -TaskName 'NaverBlog-Daily' -Confirm:`$false"
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
