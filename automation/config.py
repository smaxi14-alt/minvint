import os
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

load_dotenv(BASE_DIR / ".env")


def today_kst() -> date:
    """KST 기준 '오늘' 날짜. GitHub Actions(UTC 서버)에서 돌아가는 쓰레드
    파이프라인이 naive date.today()(=UTC 날짜)를 쓰면 아침 슬롯(07:30 KST =
    전날 22:30 UTC)의 날짜가 하루 밀린다 — content_tracker.py, content_generator.py,
    diversity_monitor.py는 date.today() 대신 이 함수를 쓴다."""
    return datetime.now(ZoneInfo("Asia/Seoul")).date()

ANTHROPIC_API_KEY           = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY               = os.getenv("GOOGLE_API_KEY", "")
NAVER_CLIENT_ID             = os.getenv("NAVER_CLIENT_ID", "").lstrip('﻿').strip()
NAVER_CLIENT_SECRET         = os.getenv("NAVER_CLIENT_SECRET", "").lstrip('﻿').strip()
BUFFER_ACCESS_TOKEN         = os.getenv("BUFFER_ACCESS_TOKEN", "")
BUFFER_CHANNEL_ID           = os.getenv("BUFFER_CHANNEL_ID", "")

NAVER_BLOG_ID               = os.getenv("NAVER_BLOG_ID", "")
NAVER_BLOG_PW                = os.getenv("NAVER_BLOG_PW", "")

# ── 셀럽 트렌드 블로그(kbyoung1120) — 보험 블로그와 별개 계정 ──────────────
CELEB_NAVER_BLOG_ID         = os.getenv("CELEB_NAVER_BLOG_ID", "")
CELEB_NAVER_BLOG_PW         = os.getenv("CELEB_NAVER_BLOG_PW", "")
PEXELS_API_KEY               = os.getenv("PEXELS_API_KEY", "")
# YouTube Data API v3 — GOOGLE_API_KEY와 같은 GCP 프로젝트에서 API만 활성화하면
# 별도 키 없이 재사용 가능(2026-07-14 실측 확인). 전용 키를 쓰고 싶으면
# .env에 YOUTUBE_API_KEY만 추가하면 이 값이 우선한다.
YOUTUBE_API_KEY               = os.getenv("YOUTUBE_API_KEY", "") or GOOGLE_API_KEY
# 인스타그램 게시물 URL 자동 발견용 (2026-07-14: 네이버 자체 검색은 프로필
# URL만 찾고 게시물 URL은 못 찾음 확인 — Google Custom Search로 site:instagram.com/p/ 검색)
# 2026-07-15 재확인: 이 API는 신규 고객에게 완전 차단돼(403 PERMISSION_DENIED)
# celeb_assets.py에서는 더 이상 쓰지 않는다. 값은 재활성화 시도 대비 그대로 둔다.
GOOGLE_CSE_API_KEY            = os.getenv("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_ID                 = os.getenv("GOOGLE_CSE_ID", "")
# Serper.dev — 구글 검색결과 프록시(위 Custom Search API가 막혀 2026-07-15 대체
# 도입). 이미지 검색(celeb_assets.get_downloaded_photos 보조 소스)과
# site:instagram.com 검색(게시물 URL 발견 + 인스타 사진 후보 확보) 양쪽에 사용.
SERPER_API_KEY                = os.getenv("SERPER_API_KEY", "")

CELEB_MAX_DAILY_POSTS = 3
CELEB_MIN_SOURCES_FOR_CONFIRMED = 2
CELEB_SUB_CATEGORIES = ["celeb_fashion", "celeb_diet", "celeb_beauty"]
CELEB_POST_HOURS = [10, 14, 19]

INSTAGRAM_HOUR   = int(os.getenv("INSTAGRAM_HOUR",   "9"))
INSTAGRAM_MINUTE = int(os.getenv("INSTAGRAM_MINUTE", "0"))

START_DATE_STR = os.getenv("START_DATE", date.today().isoformat())
START_DATE = date.fromisoformat(START_DATE_STR)

MORNING_HOUR = int(os.getenv("MORNING_HOUR", "7"))
MORNING_MINUTE = int(os.getenv("MORNING_MINUTE", "30"))
NOON_HOUR = int(os.getenv("NOON_HOUR", "12"))
NOON_MINUTE = int(os.getenv("NOON_MINUTE", "30"))
EVENING_HOUR = int(os.getenv("EVENING_HOUR", "21"))
EVENING_MINUTE = int(os.getenv("EVENING_MINUTE", "0"))
