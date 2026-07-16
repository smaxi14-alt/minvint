# 카드뉴스 자동 생성 파이프라인 — 원클릭 실행
# 실행: .\run_cardnews.ps1
# 특정 설정: .\run_cardnews.ps1 -Config my_config.json -FigmaKey YOUR_FILE_KEY

param(
    [string]$Config    = "",
    [string]$FigmaKey  = ""
)

Set-Location $PSScriptRoot

# 환경변수 체크
$env_file = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $env_file)) {
    Write-Host "[ERROR] .env 파일이 없습니다. .env.example을 복사해서 키를 입력하세요." -ForegroundColor Red
    exit 1
}

$missing = @()
$env_content = Get-Content $env_file -Raw
if ($env_content -notmatch "GOOGLE_API_KEY=\S+") { $missing += "GOOGLE_API_KEY" }
if ($env_content -notmatch "ANTHROPIC_API_KEY=\S+") { $missing += "ANTHROPIC_API_KEY" }
if ($env_content -notmatch "FIGMA_TOKEN=\S+") { $missing += "FIGMA_TOKEN" }

if ($missing.Count -gt 0) {
    Write-Host "[경고] .env에 없는 키:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    Write-Host "이미지 생성은 건너뜁니다. 기존 이미지가 있으면 검증+Figma 단계만 실행됩니다." -ForegroundColor Yellow
    Write-Host ""
}

# 파이프라인 실행
$args_list = @()
if ($Config)   { $args_list += "--config";    $args_list += $Config }
if ($FigmaKey) { $args_list += "--figma-key"; $args_list += $FigmaKey }

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host " 카드뉴스 자동 생성 파이프라인 시작" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

python card_news_pipeline.py @args_list

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=====================================" -ForegroundColor Green
    Write-Host " 완료! Figma에서 결과를 확인하세요." -ForegroundColor Green
    Write-Host " https://www.figma.com/design/b07LKxoTSXFiOJkXq5wtCZ" -ForegroundColor Green
    Write-Host "=====================================" -ForegroundColor Green
} else {
    Write-Host "[오류] 파이프라인 실행 중 문제가 발생했습니다. logs/card_news.log를 확인하세요." -ForegroundColor Red
}
