import json
import random
import logging
from datetime import date
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, START_DATE, DATA_DIR
from content_tracker import get_recent_topics

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

with open(DATA_DIR / "content_seeds.json", "r", encoding="utf-8") as f:
    SEEDS = json.load(f)

# Phase별 운영 지침
PHASE_INSTRUCTIONS = {
    1: """현재 Phase 1 (씨앗기, 운영 1~90일)입니다.
규칙:
- 보험 영업 절대 금지
- 직업(보험설계사) 언급 최소화. 자연스럽게 흘러나올 때만.
- 40대 가장 공감·어그로 콘텐츠에 집중
- 목표: "이 계정 재밌네" 인식 형성""",

    2: """현재 Phase 2 (전환기, 91~180일)입니다.
규칙:
- 보험 직접 영업 0%. 관찰자 포지션만.
- "13년 일하면서 본 건데..." 형태로 직업 자연스럽게 노출 가능
- 재테크 콘텐츠는 반드시 데이터 + 나만의 해석 포함
- 목표: "이 사람 돈 얘기도 잘하네" 신뢰 구축""",

    3: """현재 Phase 3 (수익화기, 181일~)입니다.
규칙:
- 보험 인사이트 가능. 익명 사례, 진단 체크리스트 형태로.
- 카톡 CTA는 진단 관련 글에만 1회: "카톡에서 [이름] 찾으면 무료 1차 진단 가능합니다"
- 영업 멘트는 없음. 관찰자→전문가 포지션 유지.
- 목표: "이 사람한테 증권 보여줘도 될 것 같다" 신뢰""",
}

# 슬롯별 콘텐츠 타입 매트릭스
SLOT_MATRIX = {
    "morning": {1: "agro",          2: "agro",           3: "agro"},
    "noon":    {1: "agro_finance",  2: "finance",        3: "finance_insurance"},
    "evening": {1: "agro",          2: "job_insight",    3: "diagnosis"},
}

# 콘텐츠 타입별 설정
CONTENT_CONFIG = {
    "agro": {
        "label": "어그로·공감",
        "themes": SEEDS["themes"]["agro_empathy"],
        "templates": ["confession", "reversal", "observation", "numbers", "taboo"],
        "endings": SEEDS["ending_questions"]["agro"],
        "guide": "40대 가장의 일상 공감 콘텐츠. 와이프·자식·직장·동창·나이듦·돈 격차 소재. 데이터 없어도 됨.",
    },
    "agro_finance": {
        "label": "어그로·재테크",
        "themes": SEEDS["themes"]["agro_empathy"] + SEEDS["themes"]["finance"],
        "templates": ["confession", "reversal", "shock_stat", "numbers", "observation"],
        "endings": SEEDS["ending_questions"]["agro"] + SEEDS["ending_questions"]["finance"],
        "guide": "공감 소재 또는 가벼운 재테크 관찰. 7:3 비율로 공감 위주.",
    },
    "finance": {
        "label": "재테크",
        "themes": SEEDS["themes"]["finance"],
        "templates": ["shock_stat", "two_numbers", "time_comparison", "generation_gap", "simulation"],
        "endings": SEEDS["ending_questions"]["finance"],
        "guide": "데이터 기반 재테크 인사이트. 통계 1개 + 13년차 해석 1줄 구조. 출처는 글 끝에 짧게.",
    },
    "finance_insurance": {
        "label": "재테크·보험 인사이트",
        "themes": SEEDS["themes"]["finance"] + SEEDS["themes"]["insurance_insight"],
        "templates": ["shock_stat", "checklist", "two_numbers", "observation"],
        "endings": SEEDS["ending_questions"]["finance"] + SEEDS["ending_questions"]["insight"],
        "guide": "재테크 또는 보험 관찰자 인사이트. 영업 없음. 체크리스트로 자연스러운 진단 연결.",
    },
    "job_insight": {
        "label": "직업 인사이트",
        "themes": SEEDS["themes"]["job_insight"],
        "templates": ["observation", "confession", "reversal", "numbers"],
        "endings": SEEDS["ending_questions"]["insight"] + SEEDS["ending_questions"]["agro"],
        "guide": "13년 보험 일하면서 본 패턴. 관찰자 포지션. '영업'이 아니라 '관찰'로.",
    },
    "diagnosis": {
        "label": "진단소",
        "themes": SEEDS["themes"]["diagnosis"],
        "templates": ["checklist", "shock_stat", "reversal", "observation"],
        "endings": SEEDS["ending_questions"]["insight"],
        "guide": "실제 익명 사례 또는 진단 체크리스트. 마지막에 카톡 CTA 1개 포함.",
    },
}


def get_current_phase() -> int:
    days = (date.today() - START_DATE).days
    if days < 90:
        return 1
    elif days < 180:
        return 2
    return 3


def generate_post(slot: str, phase: int | None = None) -> tuple[str, str]:
    if phase is None:
        phase = get_current_phase()

    content_type = SLOT_MATRIX[slot][phase]
    cfg = CONTENT_CONFIG[content_type]

    theme = random.choice(cfg["themes"])
    template_key = random.choice(cfg["templates"])
    template = SEEDS["templates"][template_key]
    ending = random.choice(cfg["endings"])

    recent = get_recent_topics(days=7)
    recent_str = "\n".join(f"- {t}" for t in recent[-10:]) if recent else "없음"

    system_prompt = f"""당신은 13년차 보험설계사입니다. 쓰레드(Threads) SNS에 올릴 글을 씁니다.

[페르소나]
40대 중반, 13년차 GA 소속 보험설계사. 가족 있는 가장.
또래에게 솔직하게 말하는 선배 스타일. 전문가 자랑 없음. 관찰자 포지션.

[글쓰기 규칙]
1. 200~350자 (이상적). 500자 절대 초과 금지.
2. 줄바꿈 자주 — 2~3문장마다 한 줄씩
3. 숫자는 구체적으로 (월 300만원 ❌ / 월 317만원 ✅)
4. 결론 먼저, 마지막에 질문
5. 과장 표현 절대 금지 (최고, 완벽, 꼭, 반드시)
6. 마지막 줄: 특정 그룹 호명형 질문 1개 (답 강요 없음)
7. 이모지 사용 안 함
8. 너무 격식적이지 않은 자연스러운 말투

[운영 지침]
{PHASE_INSTRUCTIONS[phase]}

[현재 슬롯·콘텐츠 유형]
{slot} 슬롯 / {cfg['label']}

[콘텐츠 지침]
{cfg['guide']}

[최근 발행 글 주제 (반복 금지)]
{recent_str}

글 내용만 출력하세요. 제목, 태그, 설명 없이 본문만."""

    user_prompt = f"""소재: {theme}
템플릿: {template['name']} — {template['structure']}
마무리 질문 예시: {ending}

위 조건으로 오늘 {slot} 슬롯에 올릴 쓰레드 글 1개를 작성하세요."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = response.content[0].text.strip()
    logger.info(f"Generated {len(text)}자 ({content_type})")
    return text, content_type
