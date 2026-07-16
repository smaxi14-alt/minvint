# 셀럽 트렌드 블로그 1일 최대 3글 자동 발행 — Windows 작업 스케줄러 등록 스크립트
# 관리자 권한 없이도 현재 사용자 계정으로 등록 가능
# 실행: PowerShell에서 .\setup_celeb_scheduler.ps1
#
# 매일 10:00 / 14:00 / 19:00 KST 3개 트리거로 run_celeb_pipeline.py를 실행한다.
# 각 트리거는 독립적으로 "오늘 이미 CELEB_MAX_DAILY_POSTS(3)건 발행됐는지"부터
# 확인하므로, 상한 도달 후에는 나머지 슬롯이 자동으로 스킵된다(중복 발행 없음).
#
# 주의: naver_blog_poster.py는 실제 브라우저 창을 띄우고 pyautogui로 마우스/
# 키보드를 직접 조작한다 — 그 시각에 PC 화면이 잠겨 있으면 이미지 업로드
# 단계가 실패할 수 있다. 보험 블로그(11:00)와 겹치지 않게 시간대를 분산했지만,
# 같은 PC에서 동시에 두 파이프라인이 도는 상황(예: 재시도 겹침)은 피할 것.

$PythonExe  = "C:\Users\ant_6\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPath = "C:\Users\ant_6\OneDrive\문서\마케팅\automation\run_celeb_pipeline.py"
$WorkDir    = "C:\Users\ant_6\OneDrive\문서\마케팅\automation"
$LogDir     = "$WorkDir\logs"

$slots = @(
    @{ Name = "CelebBlog-Morning";   Hour = 10; Minute = 0 },
    @{ Name = "CelebBlog-Afternoon"; Hour = 14; Minute = 0 },
    @{ Name = "CelebBlog-Evening";   Hour = 19; Minute = 0 }
)

foreach ($s in $slots) {
    $taskName = $s.Name
    $logFile  = "$LogDir\celeb_$($s.Name).log"

    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "기존 작업 제거: $taskName"
    }

    # 2026-07-15 실측 발견: Windows 작업 스케줄러의 "프로그램 시작" 액션은 셸 없이
    # 대상 실행 파일을 직접 호출한다 — Execute에 python.exe를 직접 지정하면
    # Argument의 ">>"/"2>&1"가 리다이렉션으로 해석되지 않고 python.exe의 명령줄
    # 인자로 그대로 전달돼 argparse가 거부하며 exit code 2로 즉시 종료된다(로그
    # 파일도 전혀 안 생김 — 실제 CelebBlog-Morning 트리거에서 재현·확인됨).
    # cmd.exe /c로 감싸 실제 셸을 거치게 해서 리다이렉션이 정상 작동하게 한다.
    $action = New-ScheduledTaskAction `
        -Execute "cmd.exe" `
        -Argument "/c `"`"$PythonExe`" `"$ScriptPath`" >> `"$logFile`" 2>&1`"" `
        -WorkingDirectory $WorkDir

    $trigger = New-ScheduledTaskTrigger `
        -Daily `
        -At "$($s.Hour):$($s.Minute.ToString('D2'))"

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 90) `
        -RestartCount 1 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -StartWhenAvailable
        # 90분: 실측(2026-07-13)상 Gemini 이미지 생성 1건이 서버 지연으로
        # 20분+ 걸리다 503으로 실패하고 재시도가 다시 17분 걸린 사례가 있었다
        # (총 40분 소요). 후보 여러 개를 순차 시도하며 이런 지연이 겹칠 수
        # 있어 30분보다 넉넉하게 잡는다. 다음 슬롯(4시간 간격)과 겹치지 않음.

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
Write-Host "확인: Get-ScheduledTask -TaskName 'CelebBlog-*' | Select-Object TaskName, State"
Write-Host "수동 1회 실행(테스트): Start-ScheduledTask -TaskName 'CelebBlog-Morning'"
Write-Host "전체 중지: Get-ScheduledTask -TaskName 'CelebBlog-*' | Disable-ScheduledTask"
Write-Host "전체 삭제: Get-ScheduledTask -TaskName 'CelebBlog-*' | Unregister-ScheduledTask -Confirm:`$false"
Get-ScheduledTask -TaskName "CelebBlog-*" | Select-Object TaskName, State
