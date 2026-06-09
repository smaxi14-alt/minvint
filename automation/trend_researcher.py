"""
트렌드 리서처 — 네이버 뉴스 API + Claude haiku

역할:
  실시간 이슈에서 40대 가장 공감/재테크/보험 소재를 자동 추출.
  content_generator가 seeds와 혼합해 최신 트렌드 반영 글을 생성하도록 지원.

흐름:
  1. 콘텐츠 타입별 쿼리로 네이버 뉴스 검색 (최신 10건)
  2. Claude haiku: 기사 제목·요약 → 40대 공감 소재 3~5개 추출
  3. 24시간 캐시 (data/trend_cache.json) → API 절약
  4. 실패 시 빈 리스트 반환 (seeds 단독 사용으로 graceful fallback)
"""

import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime
from pathlib import Path

from config import ANTHROPIC_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, DATA_DIR
from anthropic import Anthropic

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "trend_cache.json"
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────────
# 콘텐츠 타입별 검색 쿼리 설정
# ─────────────────────────────────────────────
SEARCH_QUERIES: dict[str, list[str]] = {
    "agro": [
        "40대 직장인 현실",
        "40대 가장 고민",
        "중년 남성 공감",
        "40대 월급 생활",
    ],
    "agro_finance": [
        "40대 재테크 이슈",
        "직장인 돈 관리",
        "40대 자산 현실",
    ],
    "finance": [
        "재테크 트렌드",
        "가계 부채 이슈",
        "노후 준비 현실",
        "40대 투자 고민",
    ],
    "finance_insurance": [
        "보험 절약 방법",
        "실손보험 변화",
        "보험료 부담",
    ],
    "job_insight": [
        "보험설계사 현실",
        "40대 보험 설계",
        "GA 보험 트렌드",
    ],
    "diagnosis": [
        "보험 진단 필요",
        "보험 리모델링",
        "과보험 실태",
    ],
    "economy_news": [
        "기준금리 전망",
        "환율 달러 경제",
        "주식 증시 전망",
        "노후 연금 준비",
        "가계부채 부동산",
    ],
    "book_insight": [
        "경제 투자 도서",
        "재테크 책 추천",
    ],
}


# ─────────────────────────────────────────────
# 캐시 I/O
# ─────────────────────────────────────────────

def _load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _cache_key(content_type: str) -> str:
    return f"{date.today().isoformat()}_{content_type}"


def _get_cached(content_type: str) -> list[str] | None:
    """오늘 이미 조회한 결과가 있으면 반환, 없으면 None."""
    cache = _load_cache()
    return cache.get(_cache_key(content_type))


def _set_cache(content_type: str, themes: list[str]):
    cache = _load_cache()
    # 오래된 캐시 정리 (7일 이상)
    today = date.today().isoformat()
    cache = {k: v for k, v in cache.items() if k[:10] >= str(date.today().toordinal() - 7)[:10]}
    cache[_cache_key(content_type)] = themes
    _save_cache(cache)


# ─────────────────────────────────────────────
# 네이버 뉴스 검색
# ─────────────────────────────────────────────

def _naver_search(query: str, display: int = 5) -> list[dict]:
    """네이버 뉴스 API로 기사 검색. 실패 시 빈 리스트 반환."""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []

    params = urllib.parse.urlencode({
        "query": query,
        "display": display,
        "sort": "date",  # 최신순
    })
    url = f"{NAVER_NEWS_URL}?{params}"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("items", [])
    except urllib.error.HTTPError as e:
        logger.warning(f"[trend] 네이버 API 오류 {e.code}: {query}")
        return []
    except Exception as e:
        logger.warning(f"[trend] 네이버 검색 실패: {e}")
        return []


def _collect_headlines(content_type: str) -> list[str]:
    """콘텐츠 타입에 맞는 쿼리로 뉴스 헤드라인 수집."""
    queries = SEARCH_QUERIES.get(content_type, SEARCH_QUERIES["agro"])
    headlines = []

    for query in queries[:3]:   # 최대 3개 쿼리
        items = _naver_search(query, display=4)
        for item in items:
            # HTML 태그 제거
            title = item.get("title", "").replace("<b>", "").replace("</b>", "")
            desc  = item.get("description", "").replace("<b>", "").replace("</b>", "")
            if title:
                headlines.append(f"{title} — {desc[:60]}" if desc else title)

    return headlines[:20]   # 최대 20개


# ─────────────────────────────────────────────
# Claude haiku: 소재 추출
# ─────────────────────────────────────────────

def _extract_themes_with_claude(headlines: list[str], content_type: str) -> list[str]:
    """뉴스 헤드라인에서 40대 공감/재테크/보험 소재를 추출."""
    if not headlines:
        return []

    # content_type별 소재 안내 및 일상 소재 제외 여부
    _type_cfg: dict[str, tuple[str, bool]] = {
        "agro":              ("40대 가장의 일상 공감 소재 (직장, 가족, 돈, 나이듦)", False),
        "agro_finance":      ("40대 공감 또는 재테크 관련 소재", False),
        "finance":           ("재테크·자산·부채·노후 준비 관련 소재 (데이터·통계 중심)", True),
        "finance_insurance": ("재테크 또는 보험 관련 소재", True),
        "job_insight":       ("보험설계사 13년 관찰 관련 인사이트 소재", True),
        "diagnosis":         ("보험 진단·리모델링 사례 소재", True),
        "economy_news":      ("경제뉴스 인사이트 소재 (금리·환율·주식·부동산·연금 등 경제 이슈)", True),
        "book_insight":      ("경제·재무·자기계발 책 제목·저자와 연결 가능한 인사이트 소재", True),
    }
    type_guide, exclude_agro = _type_cfg.get(content_type, ("40대 가장 공감 소재", False))

    agro_exclusion_rule = (
        "- 와이프·학원비·동창회·취미용품·자식·가족 일상 공감 소재 제외 (해당 슬롯은 경제/재테크/책 내용 전용)\n"
        if exclude_agro else ""
    )

    headlines_str = "\n".join(f"- {h}" for h in headlines)

    prompt = f"""아래는 오늘 날짜 뉴스 헤드라인 목록입니다.

이 중에서 SNS 쓰레드 글감으로 쓸 수 있는 **{type_guide}** 3~5개를 추출하세요.

조건:
- 40대 남성 직장인·가장이 공감하거나 흥미를 느낄 소재
{agro_exclusion_rule}- 영업·광고성 소재 제외
- 너무 정치적이거나 논쟁적인 소재 제외
- 각 소재는 1줄 이내 (15~30자), 구체적 상황이나 수치 포함 시 우선
- JSON 배열로만 반환 (다른 텍스트 절대 금지)

뉴스 헤드라인:
{headlines_str}

JSON 반환 (예: ["소재1", "소재2", "소재3"]):"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        themes: list[str] = json.loads(raw)
        # 문자열 리스트인지 검증
        if isinstance(themes, list) and all(isinstance(t, str) for t in themes):
            logger.info(f"[trend] 추출 소재 {len(themes)}개: {themes}")
            return themes[:5]
        return []
    except json.JSONDecodeError:
        logger.warning(f"[trend] haiku 응답 JSON 파싱 실패. 원본: {raw[:100]}")
        return []
    except Exception as e:
        logger.warning(f"[trend] haiku 소재 추출 실패: {e}")
        return []


# ─────────────────────────────────────────────
# 외부 인터페이스
# ─────────────────────────────────────────────

def get_trend_themes(content_type: str) -> list[str]:
    """
    콘텐츠 타입에 맞는 실시간 트렌드 소재 반환.
    - 오늘 이미 조회했으면 캐시 반환
    - economy_news: 네이버 뉴스 + YouTube 채널 분석 병합
    - Naver API 미설정 시 빈 리스트 (graceful fallback)
    """
    # 캐시 확인
    cached = _get_cached(content_type)
    if cached is not None:
        logger.info(f"[trend] 캐시 사용 ({_cache_key(content_type)}): {len(cached)}개")
        return cached

    themes: list[str] = []

    # 네이버 뉴스 소재
    if NAVER_CLIENT_ID and NAVER_CLIENT_SECRET:
        logger.info(f"[trend] 네이버 뉴스 검색 시작 (content_type={content_type})")
        headlines = _collect_headlines(content_type)
        logger.info(f"[trend] 헤드라인 {len(headlines)}개 수집")
        themes = _extract_themes_with_claude(headlines, content_type)
    else:
        logger.info("[trend] NAVER API 키 없음 — seeds 단독 사용")

    # 모든 타입: YouTube 채널 분석 소재 병합 (당일 캐시로 1회만 실제 분석)
    try:
        from youtube_analyzer import get_youtube_themes
        yt_themes = get_youtube_themes(content_type)
        if yt_themes:
            logger.info(f"[trend] YouTube 소재 {len(yt_themes)}개 병합 ({content_type})")
            themes = yt_themes + themes  # YouTube 소재를 앞에 배치 (우선 사용)
    except Exception as e:
        logger.warning(f"[trend] YouTube 분석 스킵: {e}")

    _set_cache(content_type, themes)
    return themes
