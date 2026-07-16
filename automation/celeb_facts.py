"""셀럽 트렌드 블로그 — STAGE 2: 교차 팩트 수집.

특정 기사 1개에 의존하지 않고, 네이버 뉴스/블로그/카페글 3개 검색 API를
동시에 호출해 서로 다른 출처의 짧은 description 스니펫만 모은다(본문 전체가
아님 — 저작권 안전, 개발 지시서 원칙 ① 준수). 2개 이상 출처에서 공통으로
확인되는 사실만 confirmed로, 단일 출처는 unconfirmed([미확인] 태그 대상)로
Claude가 정규화한다(사용자 지시서 "Claude API 활용 지점 A").
"""
import json
import logging
import urllib.request
import urllib.parse
import urllib.error

from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
import celeb_db as db
from celeb_validate import NEGATIVE_KEYWORDS

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

_ENDPOINTS = {
    "news": "https://openapi.naver.com/v1/search/news.json",
    "blog": "https://openapi.naver.com/v1/search/blog.json",
    "cafe": "https://openapi.naver.com/v1/search/cafearticle.json",
}

_FACTS_TOOL = {
    "name": "extract_facts",
    "description": "여러 출처 스니펫에서 교차 확인된 사실만 정규화해 추출한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_text": {"type": "string", "description": "사실 자체(문장 복제 금지, 새로 정규화한 표현)"},
                        "source_count": {"type": "integer"},
                        "status": {"type": "string", "enum": ["confirmed", "unconfirmed"]},
                        "hook_strength": {
                            "type": "integer",
                            "enum": [1, 2, 3],
                            "description": "이 사실이 글의 후킹 포인트로서 얼마나 강력한가. 3=반전·수치·결과 등 독자가 클릭할 이유가 되는 핵심 사실, 2=흥미롭지만 부차적, 1=단순 배경정보(소속·기본 프로필 등)",
                        },
                    },
                    "required": ["fact_text", "source_count", "status", "hook_strength"],
                },
            },
            "needs_more_facts": {
                "type": "boolean",
                "description": "confirmed 팩트가 너무 적어(글 쓰기 부족) 이 후보를 스킵해야 하면 true",
            },
        },
        "required": ["facts", "needs_more_facts"],
    },
}


def _naver_search(endpoint: str, query: str, display: int = 5) -> list[dict]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []
    params = urllib.parse.urlencode({"query": query, "display": display, "sort": "date"})
    url = f"{_ENDPOINTS[endpoint]}?{params}"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("items", [])
    except urllib.error.HTTPError as e:
        logger.warning(f"[celeb] {endpoint} API 오류 {e.code}: {query}")
        return []
    except Exception as e:
        logger.warning(f"[celeb] {endpoint} 검색 실패: {e}")
        return []


def _collect_snippets(celeb_name: str, topic: str) -> dict[str, list[str]]:
    """뉴스/블로그/카페 3종에서 각각 스니펫을 모은다. 출처별로 분리 반환
    (Claude가 "몇 종류의 출처"에서 언급됐는지 판단할 수 있게).

    쿼리는 celeb_name 단독으로만 검색한다 — 처음엔 f"{celeb_name} {topic}"으로
    검색했는데, topic이 Claude가 만든 긴 문장형 요약("패션 매거진 화보 및
    인터뷰를 통한 스타일 공개" 등)이라 네이버 검색이 사실상 매칭이 안 됐다
    (실측: "장원영" 단독 검색은 97,114건인데 결합 쿼리는 0건 — 2026-07-13).
    topic 관련성 판단은 검색 단계가 아니라 아래 Claude 팩트 추출 프롬프트가
    맡는다(celeb_name과 topic을 둘 다 프롬프트에 전달)."""
    query = celeb_name
    snippets_by_source = {}
    for endpoint in _ENDPOINTS:
        items = _naver_search(endpoint, query, display=5)
        snippets = []
        for item in items:
            title = item.get("title", "").replace("<b>", "").replace("</b>", "")
            desc = item.get("description", "").replace("<b>", "").replace("</b>", "")
            if title or desc:
                snippets.append(f"{title} — {desc}" if desc else title)
        snippets_by_source[endpoint] = snippets
    return snippets_by_source


# 2026-07-15 재설계 — 사용자 요청: "팩트 개수 부족으로 거르는 건 버리고,
# 일상적인 뷰티·패션·다이어트 가십은 필터링하지 말자. 다만 논란 소지가 있는
# (셀럽에 안 좋은) 내용이면 팩트를 꼭 확인해야 한다 — 목표는 안 좋은 내용으로
# 누군가를 공격하는 상황을 막는 것."
#
# 기존엔 서브태그(celeb_fashion/celeb_diet=완화, celeb_beauty=엄격)로 엄격도를
# 나눴는데, 오늘 실측(2026-07-15 14:00 슬롯, 장원영 사례)으로 이게 부정확하다는
# 게 드러났다 — "여름 감성 메이크업" 뷰티 소재로 검색했는데, celeb_facts.py는
# celeb_name 단독 검색이라(위 _collect_snippets 참조) 실제로 걸린 스니펫은
# 전혀 무관한 "팔짱 논란" 기사였다. 서브태그만 보면 "뷰티"라 엄격 기준(3개)이
# 적용됐어야 하는데, 실제 내용은 논란이었다 — 사전 카테고리가 아니라 사후
# 추출된 팩트 내용 자체로 판단해야 정확하다.
#
# 그래서 이제: 추출된 팩트(확인+미확인 전체) 안에 NEGATIVE_KEYWORDS(논란·열애설
# ·의혹 등, celeb_validate.py와 동일 목록 재사용)가 하나라도 있으면 "논란 소지
# 있음"으로 판단해 엄격한 confirmed 개수(_MIN_CONFIRMED_FOR_CONTROVERSY)를
# 요구한다. 없으면(일상적 가십) 개수 제한 없이 통과시키고, 미확인 팩트는
# celeb_generator.py의 기존 [미확인] 헤징 규칙이 책임 있게 처리한다. 논란
# 콘텐츠는 설령 이 문턱을 통과해도 celeb_validate.check_negative_content가
# 최종 발행 전 다시 한번 막는다(이중 안전망).
_MIN_CONFIRMED_FOR_CONTROVERSY = 3


def _has_controversy_signal(facts: list[dict]) -> bool:
    """추출된 팩트(확인·미확인 모두) 안에 논란·부정적 신호 키워드가 있는지
    확인한다. celeb_validate.NEGATIVE_KEYWORDS와 동일 목록을 재사용해 두
    단계(팩트 수집·최종 검증)의 기준이 어긋나지 않게 한다."""
    text = " ".join(f.get("fact_text", "") for f in facts)
    return any(kw in text for kw in NEGATIVE_KEYWORDS)

# 2026-07-15 신설 — "글 주제 자체가 셀럽이 SNS에 올린 특정 사진"인 경우
# (오늘 닉쿤 사례), 이미지 다운로드 검색으로는 그 사진을 원천적으로 못 찾는다
# (인덱싱 안 된 최신 게시물). 이 키워드가 스니펫에 있으면 로그로 짚어줘서,
# 기존 주 1~2회 수동 IG 워치리스트 큐레이션(celeb_review.py add-ig)이
# "이번 주 뭐가 화제인지 감으로 찾기"가 아니라 "로그가 짚어준 후보만 확인"이
# 되게 한다. 자동화는 하지 않음 — 이미 사람이 하기로 확정된 프로세스 유지.
_SNS_SOURCE_KEYWORDS = ["SNS", "인스타그램", "인스타", "게재"]


def gather_facts(celeb_name: str, topic: str, sub_tags: list[str]) -> dict:
    """교차 확인된 팩트를 수집한다.
    반환: {"facts": [{"fact_text", "source_count", "status"}, ...], "needs_more_facts": bool}
    """
    snippets_by_source = _collect_snippets(celeb_name, topic)
    total_snippets = sum(len(v) for v in snippets_by_source.values())
    logger.info(
        f"[celeb] {celeb_name} 스니펫 수집: 뉴스={len(snippets_by_source['news'])} "
        f"블로그={len(snippets_by_source['blog'])} 카페={len(snippets_by_source['cafe'])}"
    )
    if total_snippets == 0:
        return {"facts": [], "needs_more_facts": True}

    all_snippets_text = " ".join(s for snippets in snippets_by_source.values() for s in snippets)
    if any(kw in all_snippets_text for kw in _SNS_SOURCE_KEYWORDS):
        logger.info(
            f"[celeb] IG 워치리스트 후보 — 수동 큐레이션 권장: {celeb_name} "
            f"(주제가 SNS 게시물 기반 — 이미지 검색으로 원본 사진을 못 찾을 가능성 높음)"
        )

    sources_str = "\n\n".join(
        f"[{src} 출처]\n" + "\n".join(f"- {s}" for s in snippets)
        for src, snippets in snippets_by_source.items() if snippets
    )

    prompt = f"""아래는 "{celeb_name}"의 "{topic}"에 관해 뉴스/블로그/카페 3개 출처에서
수집한 짧은 스니펫입니다. 이 스니펫들에서 공통으로 확인되는 사실만 추출하세요.

규칙:
1. 기사 문장을 그대로 복사하지 말고, 사실 자체를 새 문장으로 정규화하세요.
2. 서로 다른 출처(news/blog/cafe 중 2개 이상, 또는 news 안에서도 서로 다른 매체)에서
   공통으로 확인되면 status="confirmed", source_count는 확인된 출처 개수.
3. 한 곳에서만 언급되거나 추측성 표현("~로 추정", "~라는 소문")이면
   status="unconfirmed", source_count=1.
4. 광고성·홍보성 문구(협찬 단정 등)는 팩트로 만들지 마세요.
5. needs_more_facts 판단 기준(2026-07-15 재설계 — "가십은 관대하게, 논란은
   엄격하게"):
   - 사실 내용에 논란·열애설·불화·의혹·저격·폭로·루머·구설수·비방·고소·학폭 같은
     부정적/논란 소지가 하나라도 있으면, confirmed 사실이 3개 미만일 때
     needs_more_facts=true로 설정하세요(이런 소재는 근거가 확실해야만 다룹니다).
   - 그런 부정적 신호가 전혀 없는 일상적인 뷰티·패션·다이어트 가십이라면,
     사실이 confirmed든 unconfirmed든 최소 1개만 있어도 needs_more_facts=false로
     설정하세요(개수로 막지 마세요 — 미확인 사실은 본문에서 [미확인]으로
     표시되니 괜찮습니다). 사실이 정말 하나도 없을 때만 true로 설정하세요.
6. 각 사실에 hook_strength(1~3)를 매기세요 — 3은 반전·수치·결과처럼 독자가 클릭할
   이유가 되는 핵심 사실, 2는 흥미롭지만 부차적, 1은 소속·기본 프로필 같은 단순
   배경정보입니다. (이 값은 카드뉴스형 이미지를 만들 때 가장 밋밋한 사실이 아니라
   가장 후킹되는 사실을 먼저 쓰기 위해 사용됩니다.)

출처 스니펫:
{sources_str}"""

    try:
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=3000,
            tools=[_FACTS_TOOL],
            tool_choice={"type": "tool", "name": "extract_facts"},
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "max_tokens":
            # 응답이 중간에 잘리면 tool_use.input이 불완전한 JSON이 되어 조용히
            # 빈 결과로 보일 수 있다(2026-07-13 실측: celeb_watchlist.py에서
            # 같은 원인으로 후보 0개가 나온 적 있음) — 반드시 로그로 남긴다.
            logger.warning(f"[celeb] {celeb_name} 팩트 추출 응답이 max_tokens로 잘림 — 결과 불완전할 수 있음")
        for block in resp.content:
            if block.type == "tool_use":
                result = block.input
                # 2026-07-15 실측 발견: tool_choice로 강제해도 Claude가 가끔
                # facts 항목을 스키마와 다르게(dict가 아니라 문자열 등) 반환해
                # f["status"] 접근에서 TypeError("string indices must be
                # integers")가 나며 이 함수 전체가 except로 빠져 팩트가 있는
                # 후보까지 "팩트 부족"으로 낭비되는 사례가 실제로 발생함(조이
                # 후보, CelebBlog-Afternoon 14:00 슬롯). dict 형태가 아닌
                # 항목만 걸러내 로그로 남기고, 정상 항목은 그대로 살린다.
                raw_facts = result.get("facts", [])
                valid_facts = [f for f in raw_facts if isinstance(f, dict) and "status" in f and "fact_text" in f]
                if len(valid_facts) < len(raw_facts):
                    logger.warning(
                        f"[celeb] {celeb_name}: facts 응답 중 {len(raw_facts) - len(valid_facts)}개가 "
                        f"예상 형식(dict)이 아니어서 제외됨: {[f for f in raw_facts if f not in valid_facts]}"
                    )
                result["facts"] = valid_facts
                for f in valid_facts:
                    db.add_fact(celeb_name, sub_tags, f["fact_text"], f.get("source_count", 1), f["status"])

                # 코드 레벨 강제(2026-07-15 재설계) — 프롬프트 지시(규칙 5)만
                # 믿지 않고 결정론적으로 재확인한다. NEGATIVE_KEYWORDS 신호가
                # 있는 소재는 confirmed 3개 미만이면 무조건 needs_more_facts=true
                # 로 덮어쓴다. 신호가 없는 일상적 가십은 facts가 하나라도 있으면
                # (Claude가 실수로 true를 줬어도) needs_more_facts=false로
                # 되돌린다 — "팩트 개수로 막지 않는다"는 원칙을 코드가 보장한다.
                confirmed = [f for f in valid_facts if f["status"] == "confirmed"]
                if _has_controversy_signal(valid_facts):
                    if len(confirmed) < _MIN_CONFIRMED_FOR_CONTROVERSY:
                        result["needs_more_facts"] = True
                    logger.info(
                        f"[celeb] {celeb_name}: 논란/부정적 신호 감지 — 엄격 기준 적용 "
                        f"(confirmed {len(confirmed)}/{_MIN_CONFIRMED_FOR_CONTROVERSY})"
                    )
                else:
                    result["needs_more_facts"] = len(valid_facts) == 0
                return result
        return {"facts": [], "needs_more_facts": True}
    except Exception as e:
        logger.warning(f"[celeb] 팩트 추출 실패: {e}")
        return {"facts": [], "needs_more_facts": True}


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db.init_db()

    test_celeb = sys.argv[1] if len(sys.argv) > 1 else "강재준"
    test_topic = sys.argv[2] if len(sys.argv) > 2 else "다이어트 요요"
    result = gather_facts(test_celeb, test_topic, ["celeb_diet"])
    print(f"\nneeds_more_facts: {result['needs_more_facts']}")
    for f in result["facts"]:
        print(f"  [{f['status']}, 출처{f['source_count']}] {f['fact_text']}")
