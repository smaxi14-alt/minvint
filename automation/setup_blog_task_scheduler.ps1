# 네이버 블로그 자동 발행 — Windows 작업 스케줄러 등록 스크립트
# 반드시 로컬 PC에서만 실행 (클라우드 IP는 네이버 보안 인증에 막힘)
# 실행 전 1회: python naver_blog_poster.py setup  으로 수동 로그인 필요
# 실행: PowerShell에서 .\setup_blog_task_scheduler.ps1

$PythonExe  = "C:\Users\ant_6\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPath = "C:\Users\ant_6\OneDrive\문서\마케팅\automation\post_blog_once.py"
$WorkDir    = "C:\Users\ant_6\OneDrive\문서\마케팅\automation"
$LogDir     = "$WorkDir\logs"
$LogFile    = "$LogDir\blog.log"

$taskName = "NaverBlog-AutoPost"

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "기존 작업 제거: $taskName"
}

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$ScriptPath >> `"$LogFile`" 2>&1" `
    -WorkingDirectory $WorkDir

# 기본값: 화요일·금요일 10:00 KST (주 2회 — business-context.md 블로그 장문 분석글 2편/월 기준 보수적으로 시작, 필요시 요일/시간 조정)
$trigger1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday -At "10:00"
$trigger2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday  -At "10:00"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action   $action `
    -Trigger  @($trigger1, $trigger2) `
    -Settings $settings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "등록 완료: $taskName (화/금 10:00)"
Write-Host ""
Write-Host "주의: 처음 실행 전 반드시 아래를 먼저 실행해서 로그인 세션을 저장하세요:"
Write-Host "  cd automation; python naver_blog_poster.py setup"
Write-Host ""
Write-Host "확인: Get-ScheduledTask -TaskName 'NaverBlog-*' | Select-Object TaskName, State"
Get-ScheduledTask -TaskName "NaverBlog-*" | Select-Object TaskName, State
