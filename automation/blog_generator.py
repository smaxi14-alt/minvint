import random
import re
import logging
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, BASE_DIR
from content_generator import BRAND_SAFETY_PROMPT, PHASE_INSTRUCTIONS, get_current_phase
from blog_tracker import (
    get_recent_series,
    get_recent_titles,
    get_last_hook_type,
    get_recent_metaphor_domains,
    get_recent_cta_styles,
)

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

CONTEXT_DIR = BASE_DIR.parent / "_context"

# 매 글 마지막에 고정으로 붙는 카카오 오픈채팅 링크. 본문 텍스트는 LLM이 쓰지만
# 이 링크만은 절대 LLM이 직접 타이핑하지 않는다 — 오타·서식 붕괴 리스크를 없애기
# 위해 생성 후 코드에서 항상 동일하게 덧붙인다.
KAKAO_OPENCHAT_URL = "https://open.kakao.com/o/sEevxS5h"


def _load_context(filename: str) -> str:
    path = CONTEXT_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.warning(f"컨텍스트 파일 없음: {path}")
    return ""


WRITING_PROGRAM = _load_context("blog-writing-program.md")
STYLE_GUIDE = _load_context("blog-style-guide.md")

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


def generate_blog_post(
    series: dict | None = None, keyword_spec: dict | None = None,
    avoid_violations: list[str] | None = None,
) -> dict:
    """블로그 글 생성.

    _context/blog-writing-program.md(포지셔닝 10요소·로테이션 규칙)와
    _context/blog-style-guide.md(문장·타이포 기준)를 반영해 생성한다.

    keyword_spec: automation/blog_keywords.py가 오늘 배정한 SEO 타깃 키워드.
        {"keyword": str, "tier": "A"|"B"|"C", "combo_key": str} 형태.
        주어지면 series 인자보다 우선해 series_mapping을 통해 시리즈를 결정한다
        (_context/naver-blog-6month-strategy.md §3 다양한 키워드 장악 전략 참조).

    avoid_violations: 이전 시도가 발행 전 안전검사(blog_safety_check)에서 걸린
        위반 사유 목록(2026-07-15 신설 — "막히면 멈추지 말고 고쳐서 재시도"
        요구 대응). 주어지면 content_generator.py의 기존 검증된 재생성 피드백
        패턴(Threads 파이프라인, ⚠️ 이전 생성글 거부됨 형식)을 그대로 이식해
        같은 문제를 반복하지 않도록 프롬프트에 덧붙인다. run_daily_blog.py가
        재시도 루프에서 채워 넣는다 — 이 함수 자체는 검증하지 않는다(생성만
        담당, 안전검사는 여전히 blog_safety_check.run_all_checks가 별도로
        수행).

    반환 dict 키:
        title, title_candidates, body(마크다운, [[IMAGE:...]] 마커 포함),
        series, tags, hook_type, metaphor_domain, signature, elements_used,
        image_specs(list[dict]: index/style/prompt, 최소 5개),
        keyword, tier, combo_key (keyword_spec을 그대로 반영, 없으면 빈 문자열)
    """
    if series is None:
        series = _pick_series()

    phase = get_current_phase()
    recent_titles = get_recent_titles(days=60)
    recent_str = "\n".join(f"- {t}" for t in recent_titles[-15:]) if recent_titles else "없음"

    last_hook = get_last_hook_type()
    recent_domains = get_recent_metaphor_domains(count=2)
    recent_cta_styles = get_recent_cta_styles(count=2)

    keyword_instruction = ""
    if keyword_spec and keyword_spec.get("keyword"):
        tier_label = {"A": "지역(1순위, 전환 최강)", "B": "불안·후회(2순위)", "C": "트리거 이벤트(3순위)"}.get(
            keyword_spec.get("tier", ""), keyword_spec.get("tier", "")
        )
        keyword_instruction = f"""
[오늘의 타깃 키워드 — 반드시 반영. 위반 시 글 폐기]
핵심 키워드: "{keyword_spec['keyword']}"
키워드 티어: {tier_label}
- 채택 제목(제목 후보 3안 중 1번)에 이 키워드 문구의 핵심 단어를 자연스럽게 포함하세요.
- 본문 첫 소제목(##) 또는 도입 문단(첫 200자 이내)에 이 키워드가 자연스럽게 등장해야 합니다.
- 태그 목록에 이 키워드에서 파생된 해시태그를 최소 2개 포함하세요.
- "보험 추천", "좋은 보험" 같은 판매 권유형 표현으로 변형하지 마세요 — 진단·점검·비교·확인 관점을 유지하세요.
"""

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

[포지셔닝 글쓰기 프로그램 — 반드시 준수. PART1~5 전체 적용]
{WRITING_PROGRAM}

[문장·타이포 작성 기준 — 반드시 준수]
{STYLE_GUIDE}

[이번 글 로테이션 제약 — 위반 시 글 폐기]
- 직전 글의 후킹 유형(①): {last_hook or '없음(첫 글)'} → 이번 글은 반드시 다른 유형을 사용하세요.
- 최근 비유 분야(③): {', '.join(recent_domains) if recent_domains else '없음'} → 같은 분야 3회 연속 금지. 가능하면 다른 분야를 사용하세요.
- 최근 CTA 구성안(PART 2-A): {', '.join(recent_cta_styles) if recent_cta_styles else '없음(첫 글)'} → 이번 글은 반드시 다른 구성안을 사용하세요. 8개(A~H)를 다 쓰기 전엔 재사용하지 마세요.
{keyword_instruction}
[글쓰기 규칙 — CLAUDE.md 블로그 채널 지침]
1. 분량 1,500~3,000자 (표 제외 본문 기준)
2. 두괄식 — 첫 문단에 핵심 결론/수치부터 제시
3. 실제 사례는 반드시 "익명 처리" 명시, 구체적 숫자 사용 (예: 월 78만원, 절대 "많이" 같은 뭉뚱그림 금지)
4. Before/After 비교표 등 마크다운 표 최소 1개 포함
5. 소제목(##)으로 섹션 구분 — 최소 4개 이상
6. 마지막에 CTA 섹션: `_context/blog-writing-program.md` PART 2-A의 8개 구성안 중
   위 로테이션 제약을 지키는 1개를 골라 마무리 문단을 쓰세요("증권 사진 보내주세요"류
   문구가 매번 똑같지 않도록 하는 것이 핵심입니다). 카카오 오픈채팅 URL은 절대 본문에
   직접 쓰지 마세요 — 발행 파이프라인이 본문 맨 마지막에 항상 자동으로 붙입니다.
   영업 멘트 없음을 CTA 문단 안에서 자연스럽게 드러내세요.
7. 신규 가입 유도 금지 — 기존 증권 정리·진단이 핵심
8. 특정 보험사명 직접 홍보 금지

[이미지 스펙 — 본문에 최소 5개 마커 삽입]
본문 곳곳에 아래 형식의 마커를 독립된 줄로 삽입해 이미지 삽입 위치를 지정하세요.
형식: [[IMAGE:스타일:설명]]

- 스타일은 "infographic" 또는 "photo" 중 하나만 사용:
  · infographic — 해당 문단·소제목 내용을 요약하는 심플한 인포그래픽 스타일
    (숫자·화살표·비교·아이콘 등). **대부분의 이미지는 이 스타일로 작성.**
  · photo — 감성·서사 요소(⑥ 실전 서사, ⑧ 인간적 틈)가 들어간 문단에서만
    사용하는 감성 자극형 실사(포토리얼리스틱) 이미지.
    **인물 얼굴은 절대 노출 금지** — 뒷모습, 손, 서랍 속 증권 사진, 저녁 사무실,
    창밖 풍경 등 분위기 중심으로 묘사.
- 최소 5개 마커. 첫 번째 마커는 도입부(첫 문단) 바로 다음에 배치해 대표
  이미지 역할을 하게 하세요. 이후 소제목마다 1개씩 배치.
- 설명은 nanobanana(Gemini 이미지 생성 모델)가 바로 그릴 수 있도록 구체적으로
  작성하세요.
- [블로그 전용 톤 — 카드뉴스와 다름] 색상 톤: 웜 아이보리(#F7F1E8) 또는 소프트
  네이비(#2C4270) 배경 + 톤다운 앰버(#D98E4F) 포인트. 다크 네이비+비비드 오렌지의
  차갑고 강한 대비(카드뉴스용)는 블로그에 쓰지 않는다. 부드러운 그림자·둥근
  모서리·넉넉한 여백으로 "차분하게 설명해주는 신뢰가는 선배"의 인상을 준다.
  photo 스타일은 따뜻한 자연광/은은한 실내조명(골든아워 톤)을 쓰고 차갑고
  클리니컬한 블루톤·형광 조명은 피한다.
- [사이즈 — 기존 대비 절반] nanobanana는 절대 픽셀이 아닌 aspect_ratio만 지정
  가능하다. 대표 이미지(첫 마커)는 aspect_ratio: "16:9", 본문 인포그래픽은
  "4:3" 또는 "3:2"로 생성한다. 생성 후 네이버 에디터 삽입 시 이미지 크기 조절
  핸들로 대표 600px / 본문 400px 표시 폭까지 축소한다
  (기존 1200×630px / 최대 800px 폭 대비 절반, `_context/blog-style-guide.md` §8 참조).
- [텍스트 분량 엄격 제한 — 실측 실패 사례 반영] 이미지 안에 들어갈 텍스트는 큰 숫자 1개
  + 짧은 라벨/캡션(각 8자 이내) 몇 개로만 구성하세요. 절대 문단 단위의 긴 설명글이나
  약관 발췌 같은 조밀한 본문 텍스트를 이미지 안에 요구하지 마세요 — 이미지 생성 모델은
  짧은 문구는 정확히 그리지만, 긴 문단은 실제로 뜻이 통하지 않는 한글 글자로 깨집니다
  (실측: "무보험차상해 5억, 10억"처럼 큰 숫자는 정확했지만 그 아래 설명 문단을 요청하자
  "일칠을 주역하는 잘정"처럼 의미 없는 글자로 렌더링된 사례가 있었습니다).
- [체크리스트·표 항목은 반드시 실제 문구를 직접 명시] "3개 항목 중 2개는 체크 표시"처럼
  서식만 설명하지 말고, 각 항목의 실제 텍스트를 큰따옴표로 직접 쓰세요. 예:
  '체크리스트 3개: "중복 가입 확인"(체크), "보장 공백 확인"(체크), "약관 재검토"(빈칸)'.
  서식 설명만 있으면 이미지 모델이 그 서식 설명 문장 자체를 글자로 그대로 그려버립니다
  (실측 실패 사례: 체크리스트 항목에 "3개 항목 중 2개는 톤다운 앰버 체크"라는 지시문
  자체가 문자 그대로 렌더링된 적이 있었습니다).

[출력 형식]
아래 형식 그대로, 다른 설명 없이 출력하세요.

## 제목 후보 3안
1. (SEO 키워드 포함 제목 1)
2. (SEO 키워드 포함 제목 2)
3. (SEO 키워드 포함 제목 3)

## 본문
(마크다운 본문 — 소제목, 표, [[IMAGE:스타일:설명]] 마커 최소 5개, CTA 마무리 문단 포함.
카카오 오픈채팅 URL은 쓰지 마세요 — 자동으로 붙습니다)

## 태그
`#태그1` `#태그2` ... (8~10개, SEO 키워드 중심)

## 후킹유형
(이번 글에서 사용한 ① 유형의 알파벳 1개만. 예: F)

## 비유분야
(이번 글에서 사용한 ③ 비유 분야명. 예: 통신/구독. 비유를 안 썼으면 "없음")

## 시그니처
(이번 글에 사용한 ⑦ 시그니처 문장 원문 그대로. 없으면 "없음")

## 사용요소
(이번 글에 사용한 선택 요소 코드를 쉼표로 구분. 예: ④A, ⑤B, ⑧C)

## CTA구성안
(PART 2-A에서 이번 글 마무리에 사용한 구성안 알파벳 1개 + 이름. 예: E 시간·기회비용형)

[최근 60일 발행 제목 — 이 소재·사례와 겹치지 않게 작성]
{recent_str}
"""

    if avoid_violations:
        system_prompt += f"""

[⚠️ 이전 시도 거부됨 — 반드시 피해서 다시 작성]
직전 시도가 발행 전 안전검사에서 다음 사유로 거부됐습니다:
{chr(10).join(f"- {v}" for v in avoid_violations)}
같은 표현·패턴을 다시 쓰지 말고, 이 문제를 피해서 처음부터 새로 작성하세요."""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4500,
        messages=[{"role": "user", "content": "위 지침에 따라 블로그 글을 작성하세요."}],
        system=system_prompt,
    )
    raw = response.content[0].text.strip()
    logger.info(f"블로그 글 생성 완료 ({len(raw)}자, series={series['name']})")

    parsed = _parse_sections(raw)
    chosen_title = parsed["titles"][0] if parsed["titles"] else series["example"]
    image_specs = extract_image_specs(parsed["body"])

    if len(image_specs) < 5:
        logger.warning(f"이미지 스펙이 {len(image_specs)}개뿐입니다 (최소 5개 요청함)")

    # 카카오 오픈채팅 링크는 LLM이 아니라 항상 여기서 동일하게 덧붙인다
    # (오타·서식 붕괴 방지, blog-writing-program.md PART 2-A 참조).
    body_with_cta = (
        parsed["body"].rstrip()
        + f"\n\n---\n\n카카오 오픈채팅 상담: {KAKAO_OPENCHAT_URL}"
    )

    return {
        "title": chosen_title,
        "title_candidates": parsed["titles"],
        "body": body_with_cta,
        "series": series["name"],
        "tags": parsed["tags"],
        "hook_type": parsed["hook_type"],
        "metaphor_domain": parsed["metaphor_domain"],
        "signature": parsed["signature"],
        "elements_used": parsed["elements_used"],
        "cta_style": parsed["cta_style"],
        "image_specs": image_specs,
        "keyword": (keyword_spec or {}).get("keyword", ""),
        "tier": (keyword_spec or {}).get("tier", ""),
        "combo_key": (keyword_spec or {}).get("combo_key", ""),
    }


def extract_image_specs(body: str) -> list[dict]:
    """본문 마크다운에서 [[IMAGE:스타일:설명]] 마커를 순서대로 추출한다.
    반환: [{"index": 0, "style": "infographic", "prompt": "..."}, ...]
    """
    specs = []
    for i, m in enumerate(re.finditer(r"\[\[IMAGE:(infographic|photo):(.+?)\]\]", body)):
        specs.append({"index": i, "style": m.group(1), "prompt": m.group(2).strip()})
    return specs


def _parse_sections(raw: str) -> dict:
    def _section(name: str, next_name: str | None) -> str:
        if next_name:
            pattern = rf"##\s*{name}\s*\n(.*?)(?=\n##\s*{next_name})"
        else:
            pattern = rf"##\s*{name}\s*\n(.*)"
        m = re.search(pattern, raw, re.S)
        return m.group(1).strip() if m else ""

    titles: list[str] = []
    title_block = _section("제목 후보.*?", "본문")
    for line in title_block.splitlines():
        line = line.strip()
        m = re.match(r"^\d+\.\s*\**(.+?)\**$", line)
        if m:
            titles.append(m.group(1).strip())

    body = _section("본문", "태그")

    tag_block = _section("태그", "후킹유형")
    tags = re.findall(r"#([^\s`#]+)", tag_block)

    hook_type = _section("후킹유형", "비유분야").strip("* \n")
    metaphor_domain = _section("비유분야", "시그니처").strip("* \n")
    if metaphor_domain == "없음":
        metaphor_domain = ""
    signature = _section("시그니처", "사용요소").strip("* \n")
    if signature == "없음":
        signature = ""

    elements_block = _section("사용요소", "CTA구성안")
    elements_used = [e.strip() for e in elements_block.split(",") if e.strip()]

    cta_style = _section("CTA구성안", None).strip("* \n")

    return {
        "titles": titles,
        "body": body,
        "tags": tags,
        "hook_type": hook_type,
        "metaphor_domain": metaphor_domain,
        "signature": signature,
        "elements_used": elements_used,
        "cta_style": cta_style,
    }
