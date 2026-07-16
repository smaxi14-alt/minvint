# 인스타그램 카드뉴스 큐 일일 스캐너 — 원클릭 실행 (알림 전용, 이미지 생성 없음)
# 실행: .\run_daily_scanner.ps1
#
# automation/ 폴더의 쓰레드·블로그 파이프라인과 완전히 독립된 폴더에서 동작한다
# (2026-07-16, 사용자 요청 — "쓰레드 로직은 건드리지 말고 인스타 자동발행은
# 완전히 다른 폴더로"). automation/logs/posts_log.json, blog_log.json을 읽기
# 전용으로만 참조하며, automation/의 파이썬 코드는 한 줄도 import하지 않는다.

Set-Location $PSScriptRoot

$env_file = Join-Path $PSScriptRoot "..\automation\.env"
if (-not (Test-Path $env_file)) {
    Write-Host "[ERROR] automation/.env 파일이 없습니다." -ForegroundColor Red
    exit 1
}

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host " 인스타그램 카드뉴스 큐 일일 스캔 시작" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

python daily_scanner.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=====================================" -ForegroundColor Green
    Write-Host " 스캔 완료. 선정된 후보가 있으면 Claude Code 세션에서" -ForegroundColor Green
    Write-Host " '카드뉴스 큐 확인해줘'라고 요청해 이미지 생성을 진행하세요." -ForegroundColor Green
    Write-Host "=====================================" -ForegroundColor Green
} else {
    Write-Host "[오류] 스캔 중 문제가 발생했습니다. logs/ 를 확인하세요." -ForegroundColor Red
}
