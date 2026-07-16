"""셀럽 트렌드 블로그 — STAGE 0~1: 트렌드 후보 감지.

사용자 개발 지시서는 "무엇을 검색할지"(어떤 셀럽 이름으로 검색할지)의 출발점을
명시하지 않았다 — 이 모듈이 그 공백을 채운다. 연예 관련 중립 쿼리로 네이버 뉴스를
폭넓게 검색해 헤드라인을 모으고, Claude로 "셀럽명 + 서브태그 + 주제"를 구조화
추출한다(trend_researcher.py의 뉴스 검색 → Claude 소재 추출 패턴을 그대로 확장).

기사 "제목·요약(description)"만 신호로 쓴다 — 본문 전체를 가져오지 않으므로
저작권 문제가 없다(개발 지시서 원칙 ①: 기사 원문을 콘텐츠 입력으로 쓰지 않는다.
여기서는 콘텐츠 입력이 아니라 "무엇이 화제인지" 감지하는 신호로만 쓴다).
"""
import json
import logging
import urllib.request
import urllib.parse
import urllib.error

from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
import celeb_db as db

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

# 서브태그별 중립 검색 쿼리 — trend_researcher.py의 SEARCH_QUERIES 패턴 재사용
SUB_TAG_QUERIES: dict[str, list[str]] = {
    "celeb_fashion": ["공항패션", "시상식 룩", "착용 브랜드 화제"],
    "celeb_diet": ["다이어트 성공", "체중 감량 공개", "바디프로필"],
    "celeb_beauty": ["메이크업 화제", "스킨케어 루틴", "헤어스타일 변화"],
}

_EXTRACT_TOOL = {
    "name": "extract_celeb_candidates",
    "description": "뉴스 헤드라인에서 셀럽 트렌드 후보를 구조화 추출한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "celeb_name": {"type": "string"},
                        "topic": {"type": "string", "description": "무엇이 화제인지 한 줄 요약"},
                        "sub_tags": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["celeb_fashion", "celeb_diet", "celeb_beauty"]},
                        },
                        "mention_count": {"type": "integer", "description": "이 후보를 언급한 헤드라인 개수"},
                        "gender": {"type": "string", "enum": ["female", "male", "unknown"]},
                        "popular_with_women": {
                            "type": "boolean",
                            "description": "gender가 male일 때만 의미 있음 — 여성 팬층에게 특히 인기가 많은 남자 연예인인가(예: 배우·아이돌로 여성 팬덤이 두드러진 경우). female이면 이 값은 무시된다.",
                        },
                    },
                    "required": ["celeb_name", "topic", "sub_tags", "mention_count", "gender", "popular_with_women"],
                },
            },
        },
        "required": ["candidates"],
    },
}


def _naver_search(query: str, display: int = 10) -> list[dict]:
    """네이버 뉴스 API 검색. 실패 시 빈 리스트 반환 (trend_researcher.py와 동일 패턴)."""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        logger.warning("[celeb] NAVER_CLIENT_ID/SECRET 없음 — 트렌드 감지 불가")
        return []
    params = urllib.parse.urlencode({"query": query, "display": display, "sort": "date"})
    url = f"{NAVER_NEWS_URL}?{params}"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("items", [])
    except urllib.error.HTTPError as e:
        logger.warning(f"[celeb] 네이버 API 오류 {e.code}: {query}")
        return []
    except Exception as e:
        logger.warning(f"[celeb] 네이버 검색 실패: {e}")
        return []


def _collect_headlines() -> list[str]:
    """모든 서브태그 쿼리로 헤드라인을 폭넓게 수집한다."""
    headlines = []
    for sub_tag, queries in SUB_TAG_QUERIES.items():
        for query in queries:
            items = _naver_search(query, display=8)
            for item in items:
                title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                desc = item.get("description", "").replace("<b>", "").replace("</b>", "")
                if title:
                    headlines.append(f"{title} — {desc[:60]}" if desc else title)
    return headlines


def _extract_candidates(headlines: list[str]) -> list[dict]:
    """Claude로 헤드라인에서 셀럽+서브태그+주제 후보를 구조화 추출한다.
    tool_choice로 JSON 스키마를 강제해 정규식 파싱보다 신뢰도를 높인다.
    """
    if not headlines:
        return []
    headlines_str = "\n".join(f"- {h}" for h in headlines)
    prompt = f"""아래는 오늘 수집한 연예 뉴스 헤드라인 목록입니다.

각 헤드라인에서 실제로 화제가 되는 셀럽(연예인)의 이름과, 무엇이 화제인지, 그리고
패션(celeb_fashion)/다이어트(celeb_diet)/뷰티(celeb_beauty) 중 어디에 해당하는지를
추출하세요. 하나의 헤드라인이 여러 서브태그에 걸치면 전부 표시하세요.

이 블로그의 주 타깃은 여성 독자입니다. 각 후보의 성별(gender)을 판단하고,
남성(male)인 경우 여성 팬층에게 특히 인기가 많은지(popular_with_women)도
판단하세요(예: 여성 팬덤이 두드러진 배우·아이돌·그룹 멤버는 true).

조건:
- 긍정·중립 소재만 (부정·비방·루머성 헤드라인은 제외)
- 같은 셀럽이 여러 헤드라인에 등장하면 mention_count를 합산하고 항목은 1개로 통합
- 셀럽 이름이 명확하지 않거나(그룹명만 있고 개인 특정 불가 등) 패션/다이어트/뷰티와
  무관한 헤드라인은 제외

헤드라인:
{headlines_str}"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_celeb_candidates"},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "max_tokens":
            # 응답이 중간에 잘리면 tool_use.input이 불완전한 JSON이 되어 조용히
            # 빈 결과로 보일 수 있다(2026-07-13 실측 — 후보 0개로 나왔던 원인).
            logger.warning("[celeb] 후보 추출 응답이 max_tokens로 잘림 — 결과 불완전할 수 있음")
        for block in resp.content:
            if block.type == "tool_use":
                candidates = block.input.get("candidates", [])
                if not candidates and resp.stop_reason == "max_tokens":
                    logger.warning("[celeb] 잘린 응답으로 후보 0개 — 재시도 권장")
                return candidates
        return []
    except Exception as e:
        logger.warning(f"[celeb] 후보 추출 실패: {e}")
        return []


def discover_candidates(exclude_days: int = 14) -> list[dict]:
    """오늘의 셀럽 트렌드 후보를 감지하고 signal_score로 정렬해 반환한다.
    최근 N일간 이미 발행한 셀럽은 제외한다(반복 방지).
    반환: [{"celeb_name", "topic", "sub_tags", "signal_score"}, ...] (내림차순)
    """
    headlines = _collect_headlines()
    logger.info(f"[celeb] 헤드라인 {len(headlines)}개 수집")
    if not headlines:
        return []

    raw_candidates = _extract_candidates(headlines)
    logger.info(f"[celeb] 후보 {len(raw_candidates)}개 추출")

    recent_celebs = set(db.get_recent_published_celebs(days=exclude_days))
    candidates = []
    for c in raw_candidates:
        if c["celeb_name"] in recent_celebs:
            logger.info(f"[celeb] 최근 {exclude_days}일 내 이미 발행 — 제외: {c['celeb_name']}")
            continue
        # 여성 타깃 블로그 — 남성 연예인은 여성 팬층 인기가 확인된 경우만 통과
        # (2026-07-14 사용자 요청). gender 미판단(구버전 캐시 등) 시 통과시켜
        # 과도한 제외를 막는다.
        if c.get("gender") == "male" and not c.get("popular_with_women", False):
            logger.info(f"[celeb] 여성 타깃 기준 미달(남성, 여성 인기 미확인) — 제외: {c['celeb_name']}")
            continue
        signal_score = float(c.get("mention_count", 1))
        candidates.append({
            "celeb_name": c["celeb_name"],
            "topic": c["topic"],
            "sub_tags": c["sub_tags"],
            "signal_score": signal_score,
        })
        db.add_trend_candidate(c["celeb_name"], c["topic"], c["sub_tags"], signal_score)

    candidates.sort(key=lambda c: c["signal_score"], reverse=True)
    return candidates


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db.init_db()
    result = discover_candidates()
    print(f"\n오늘의 후보 {len(result)}개:")
    for c in result:
        print(f"  [{c['signal_score']:.0f}] {c['celeb_name']} — {c['topic']} {c['sub_tags']}")
