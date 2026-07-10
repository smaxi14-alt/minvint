import random
import logging
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY
from content_generator import BRAND_SAFETY_PROMPT, PHASE_INSTRUCTIONS, get_current_phase
from blog_tracker import get_recent_series, get_recent_titles

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# _context/business-context.md "핵심 콘텐츠 시리즈" 표 그대로 반영
BLOG_SERIES = [
    {
        "name": "증권 진단 실사례",
        "target": "김부장(불안형 과보험자)",
        "guide": "실제 익명 상담 사례 1건을 Before/After 수치로 풀어낸다. 월 보험료·가입건수·중복/공백 항목을 표로 정리.",
        "example": "실제 47세 고객 증권, 월 22만원 줄인 과정",
    },
    {
        "name": "보험료 다이어트",
        "target": "김부장",
        "guide": "특정 보장 항목(암/실손/종신 등) 진단비 비교 중심. 표로 상품군별 차이를 보여준다.",
        "example": "암보험 진단비 5천 vs 3천, 차이 나는 순간",
    },
    {
        "name": "은퇴 현금흐름",
        "target": "박과장(합리형 점검자)",
        "guide": "은퇴 후 연금·저축성 보험 시뮬레이션. 연령별 수령액 표 또는 30년 시뮬레이션 형태.",
        "example": "60세부터 월 300만원, 실제 설계 가능한가",
    },
    {
        "name": "GA 설계사의 솔직한 말",
        "target": "전체",
        "guide": "GA 관찰자 시점에서 보험사별/상품군별 장단점을 비교. 영업 톤 절대 금지, 관찰자 시선 유지.",
        "example": "GA 설계사가 말하는 보험사별 진짜 장단점",
    },
    {
        "name": "40대 보험 공백 시리즈",
        "target": "김부장",
        "guide": "40대가 놓치기 쉬운 보장 공백 TOP N 형태. 체크리스트 + 표로 정리.",
        "example": "40대 가장이 놓치는 보험 공백 TOP 3",
    },
]


def _pick_series() -> dict:
    """최근 30일간 적게 쓰인 시리즈를 우선 선택 — 다양성 확보."""
    recent = get_recent_series(days=30)
    weights = []
    for s in BLOG_SERIES:
        count = recent.count(s["name"])
        weights.append(1.0 / (count + 1))
    return random.choices(BLOG_SERIES, weights=weights, k=1)[0]


def generate_blog_post(series: dict | None = None) -> tuple[str, list[str], str, str, list[str]]:
    """블로그 글 생성.

    반환: (선택된_제목, 제목_후보_3개, 본문_마크다운, 시리즈명, 태그_목록)
    """
    if series is None:
        series = _pick_series()

    phase = get_current_phase()
    recent_titles = get_recent_titles(days=60)
    recent_str = "\n".join(f"- {t}" for t in recent_titles[-15:]) if recent_titles else "없음"

    system_prompt = f"""당신은 48세 재무상담사입니다. 네이버 블로그에 올릴 SEO 장문 포스팅을 씁니다.

[이 사람이 누구인가]
13년차 GA 소속 보험설계사. 고2 아들(첫째), 중1 딸(둘째)을 둔 가장.
데이터·숫자 기반 객관적 분석이 강점. 얼굴 노출 없이 목소리와 숫자로 신뢰를 쌓는다.

[이번 글 시리즈]
{series['name']} (타깃: {series['target']})
{series['guide']}
참고 예시 제목(그대로 쓰지 말고 변형): "{series['example']}"

{BRAND_SAFETY_PROMPT}

[운영 지침]
{PHASE_INSTRUCTIONS[phase]}

[글쓰기 규칙 — CLAUDE.md 블로그 채널 지침]
1. 분량 1,500~3,000자 (표 제외 본문 기준)
2. 두괄식 — 첫 문단에 핵심 결론/수치부터 제시
3. 실제 사례는 반드시 "익명 처리" 명시, 구체적 숫자 사용 (예: 월 78만원, 절대 "많이" 같은 뭉뚱그림 금지)
4. Before/After 비교표 등 마크다운 표 최소 1개 포함
5. 소제목(##)으로 섹션 구분 — 최소 4개 이상
6. 마지막에 CTA 섹션: 카카오톡 채널로 증권 사진 보내면 무료 1차 진단, 영업 멘트 없음을 명시
7. 과장 표현 절대 금지 (최고, 완벽, 꼭, 반드시)
8. "~해야 합니다" 설교체 금지 — 관찰자 어투 유지
9. 신규 가입 유도 금지 — 기존 증권 정리·진단이 핵심
10. 특정 보험사명 직접 홍보 금지

[출력 형식]
아래 형식 그대로, 다른 설명 없이 출력하세요.

## 제목 후보 3안
1. (SEO 키워드 포함 제목 1)
2. (SEO 키워드 포함 제목 2)
3. (SEO 키워드 포함 제목 3)

## 본문
(마크다운 본문 — 소제목, 표, CTA 포함)

## 태그
`#태그1` `#태그2` ... (8~10개, SEO 키워드 중심)

[최근 60일 발행 제목 — 이 소재·사례와 겹치지 않게 작성]
{recent_str}
"""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4000,
        messages=[{"role": "user", "content": "위 지침에 따라 블로그 글을 작성하세요."}],
        system=system_prompt,
    )
    raw = response.content[0].text.strip()
    logger.info(f"블로그 글 생성 완료 ({len(raw)}자, series={series['name']})")

    title_candidates, body, tags = _parse_sections(raw)
    chosen_title = title_candidates[0] if title_candidates else series["example"]
    return chosen_title, title_candidates, body, series["name"], tags


def _parse_sections(raw: str) -> tuple[list[str], str, list[str]]:
    import re

    titles: list[str] = []
    title_match = re.search(r"##\s*제목 후보.*?\n(.*?)(?=\n##\s*본문)", raw, re.S)
    if title_match:
        for line in title_match.group(1).strip().splitlines():
            line = line.strip()
            m = re.match(r"^\d+\.\s*\**(.+?)\**$", line)
            if m:
                titles.append(m.group(1).strip())

    body_match = re.search(r"##\s*본문\s*\n(.*?)(?=\n##\s*태그)", raw, re.S)
    body = body_match.group(1).strip() if body_match else raw

    tags: list[str] = []
    tag_match = re.search(r"##\s*태그\s*\n(.*)", raw, re.S)
    if tag_match:
        tags = re.findall(r"#([^\s`#]+)", tag_match.group(1))

    return titles, body, tags
