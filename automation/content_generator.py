import json
import random
import logging
from anthropic import Anthropic
from config import ANTHROPIC_API_KEY, START_DATE, DATA_DIR, today_kst
from content_tracker import get_recent_topics, get_recent_usage, get_recent_post_texts
from performance_analyzer import get_top_patterns, build_weight_boosts, format_for_prompt
from persona_manager import get_persona_summary
from trend_researcher import get_trend_themes
import market_data

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

# 소재(theme) 하드 쿨다운 — 이 기간 내 쓴 시드는 후보 풀에서 아예 제외한다.
# 2026-07-24 발견: theme도 template/tone/ending과 똑같이 14일 소프트 패널티만 걸려 있었는데,
# 14일이 지나면 recent_counts=0이 되어 "한 번도 안 쓴 소재"와 완전히 같은 확률로 재선택됐다.
# 그 결과 고정 시드 문자열("아이 방에서 자는 척하다 들은 말")이 18일 간격으로 재발행됨.
# theme은 고정 문자열이라 반복 티가 크게 나므로 template/tone과 달리 소프트가 아닌 하드 컷을 둔다.
# 주의: 시드 풀이 (쿨다운일수 × 하루 소비 슬롯수)보다 작으면 폴이 매번 바닥나 결국 다시
# 반복된다 — diversity_monitor.py의 소재풀 소진 경고를 참고해 풀을 늘려야 한다.
THEME_COOLDOWN_DAYS = 60

TONES = [
    {"name": "건조한",   "weight": 30, "instruction": "건조하고 담담한 어투. 감정 표현 최소화. 사실만 전달."},
    {"name": "직접적",   "weight": 25, "instruction": "직접적이고 단호한 어투. 서론 없이 바로 핵심부터."},
    {"name": "회고적",   "weight": 20, "instruction": "회고적이고 사색적인 어투. 천천히 생각하며 쓰는 느낌."},
    {"name": "성찰적",   "weight": 15, "instruction": "과거를 성찰하는 여유 있는 어투. 경험에서 배운 것을 담담하게. 자조나 열등감 없이."},
    {"name": "위트있는", "weight": 10, "instruction": "약간 위트 있고 가벼운 어투. 과장 없이 자연스러운 반전."},
]

LENGTHS = [
    {"name": "짧은 글", "weight": 25, "instruction": "100~170자. 5줄 이내. 핵심만 남기고 다 쳐냄."},
    {"name": "중간 글", "weight": 50, "instruction": "200~320자. 6~9줄. 기본 구조 충실히."},
    {"name": "긴 글",   "weight": 25, "instruction": "330~480자. 10줄 이상. 풍부한 서술. 단 500자 초과 금지."},
]


def _weighted_pick(items: list) -> dict:
    weights = [item["weight"] for item in items]
    return random.choices(items, weights=weights, k=1)[0]


def _diversity_pick(items: list, recent_counts: dict, weight_key: str = "name") -> any:
    """최근 사용 빈도를 반영해 다양성을 보장하며 선택.

    - items가 dict 리스트일 경우: weight_key 필드로 최근 사용 횟수 조회,
      base weight에 패널티(1/(사용횟수+1)) 적용.
    - items가 str 리스트일 경우: 각 문자열을 키로 조회해 패널티 적용.
    """
    if not items:
        return None

    if isinstance(items[0], dict):
        weights = []
        for item in items:
            key = item.get(weight_key, "")
            base = item.get("weight", 1)
            count = recent_counts.get(key, 0)
            # 최근 1회 → 0.5배, 2회 → 0.33배, 3회+ → 0.25배
            penalty = 1.0 / (count + 1) if count > 0 else 1.0
            weights.append(base * penalty)
        return random.choices(items, weights=weights, k=1)[0]
    else:
        weights = []
        for item in items:
            count = recent_counts.get(item, 0)
            penalty = 1.0 / (count + 1) if count > 0 else 1.0
            weights.append(penalty)
        return random.choices(items, weights=weights, k=1)[0]

# ─── 브랜드 안전 규칙 ─────────────────────────────────────────────────────────
# 재무상담사 신뢰 자산 보호. 이 규칙을 위반하는 글은 발행 전 폐기·재생성한다.
BRAND_SAFETY_PROMPT = """
[브랜드 안전 규칙 — 절대 위반 금지]
이 계정은 재무상담사 신뢰 자산 구축이 목적이다.
아래 소재·톤이 포함된 글을 쓰면 잠재 고객의 신뢰를 즉시 잃는다.

✕ 재정 결핍 신호: "통장 잔고 없다", "돈이 없어서 못", "삼각김밥", "편의점 끼니", "축의금 얼마 내야", "적금 못 깨고" 류
✕ 열등감·비교 패배: 동창·친구가 잘 사는 것을 부러워하거나 내가 뒤처진 느낌
✕ 재정 박탈감: 지출 부담이 커서 못 한다, 형편이 안 된다는 뉘앙스
✕ 자기비하: "나만 아직 월급쟁이", "나는 못 모았다", 자조 섞인 패배감

○ 대신 이렇게 써라: 여유 있는 관찰자, 먼저 정리된 선배, 베푸는 사람의 시선
○ 비교는 "내가 부러움"이 아니라 "내가 관찰한 패턴" 형태로만
"""

BRAND_SAFETY_CHECK_PROMPT = """다음 SNS 글이 재무상담사의 신뢰 이미지를 훼손하는지 판단하세요.

훼손 기준:
1. 재정 부족·결핍을 직접 드러냄 (통장 없다, 못 산다, 축의금 고민 등)
2. 동창·친구에 비해 내가 열등하거나 뒤처진 느낌을 표현함
3. 경제적 박탈감·자조감이 글의 핵심 감정임
4. 재무상담사에게 돈을 맡기고 싶지 않게 만드는 내용임

판단: FAIL 또는 PASS 한 단어만 반환.
FAIL이면 콜론 뒤에 이유 한 줄 추가. 예) FAIL: 동창보다 뒤처진 열등감이 글 전체를 관통함
PASS면 그냥 PASS만."""

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
# YouTube 소재 70% / 어그로·일상 20% / 재테크 10% 믹스 기준
# morning=어그로(20%), noon=YouTube경제뉴스(35%), evening=YouTube직업인사이트(35%)
# Phase 1 evening: agro_finance(공감+재테크 혼합)로 재테크 10% 커버
SLOT_MATRIX = {
    "morning": {1: "agro",         2: "agro",          3: "agro"},
    "noon":    {1: "economy_news", 2: "economy_news",   3: "economy_news"},
    "evening": {1: "agro_finance", 2: "job_insight",    3: "job_insight"},
}

# 콘텐츠 타입별 설정
CONTENT_CONFIG = {
    "agro": {
        "label": "어그로·공감",
        "themes": SEEDS["themes"]["agro_empathy"],
        "templates": ["confession", "reversal", "observation", "numbers", "taboo"],
        "guide": "40대 가장의 일상 공감 콘텐츠. 자식·직장·나이듦·건강·가족 소재 중심. 여유 있는 관찰자 시선 유지. 재정 부족·열등감·동창 비교 소재 절대 사용 금지.",
    },
    "agro_finance": {
        "label": "어그로·재테크",
        "themes": SEEDS["themes"]["agro_empathy"] + SEEDS["themes"]["finance"],
        "templates": ["confession", "reversal", "shock_stat", "numbers", "observation"],
        "guide": "공감 소재 또는 가벼운 재테크 관찰. 7:3 비율로 공감 위주. 재정 결핍·남 부러워하기·동창 비교 소재 절대 사용 금지. 관찰자·선배 포지션 유지.",
    },
    "finance": {
        "label": "재테크",
        "themes": SEEDS["themes"]["finance"],
        "templates": ["shock_stat", "two_numbers", "time_comparison", "generation_gap", "simulation"],
        "guide": "데이터 기반 재테크 인사이트. 통계 1개 + 13년차 해석 1줄 구조. 출처는 글 끝에 짧게.",
    },
    "finance_insurance": {
        "label": "재테크·보험 인사이트",
        "themes": SEEDS["themes"]["finance"] + SEEDS["themes"]["insurance_insight"],
        "templates": ["shock_stat", "checklist", "two_numbers", "observation"],
        "guide": "재테크 또는 보험 관찰자 인사이트. 영업 없음. 체크리스트로 자연스러운 진단 연결.",
    },
    "job_insight": {
        "label": "직업 인사이트",
        "themes": SEEDS["themes"]["job_insight"],
        "templates": ["observation", "confession", "reversal", "numbers"],
        "guide": "13년 보험 일하면서 본 패턴. 관찰자 포지션. '영업'이 아니라 '관찰'로.",
    },
    "diagnosis": {
        "label": "진단소",
        "themes": SEEDS["themes"]["diagnosis"],
        "templates": ["checklist", "shock_stat", "reversal", "observation"],
        "guide": "실제 익명 사례 또는 진단 체크리스트. 마지막에 카톡 CTA 1개 포함.",
    },
    "economy_news": {
        "label": "경제뉴스 인사이트",
        "themes": SEEDS["themes"]["economy_news"],
        "templates": ["shock_stat", "two_numbers", "time_comparison", "reversal", "observation"],
        "guide": "오늘 경제뉴스 1개를 13년차 재무상담사 관점으로 해석. '기사는 이렇게 썼지만 실제 의미는...' 구조. 핵심 1~2줄 + 내 한 마디. 출처는 자연스럽게 짧게 언급.",
    },
    "book_insight": {
        "label": "책 인사이트",
        "themes": SEEDS["themes"]["book_insight"],
        "templates": ["observation", "reversal", "confession", "numbers"],
        "guide": "읽은 책 한 구절 또는 핵심 개념 + 13년 상담 경험과 연결. 독후감 형식 아님 — 짧은 생각 공유. '책에서 이런 말 봤는데 실제로 상담하다 보면...' 연결 구조.",
    },
}


def _brand_safety_check(text: str) -> tuple[bool, str]:
    """생성된 글이 재무상담사 신뢰 이미지를 훼손하는지 Claude haiku로 검사.
    반환: (통과: bool, 이유: str) — 통과하면 (True, ""), 실패하면 (False, 이유)
    """
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": f"{BRAND_SAFETY_CHECK_PROMPT}\n\n[글]\n{text}"}],
        )
        result = response.content[0].text.strip()
        if result.upper().startswith("FAIL"):
            reason = result.split(":", 1)[-1].strip() if ":" in result else result
            logger.warning(f"[brand_safety] FAIL — {reason}")
            return False, reason
        return True, ""
    except Exception as e:
        logger.warning(f"[brand_safety] 검사 실패 (통과 처리): {e}")
        return True, ""


CONSISTENCY_CHECK_PROMPT = """아래는 같은 SNS 계정(48세 재무상담사 페르소나)이 과거에 발행한 글
목록[과거 발행 글 목록]이고, 그 뒤에 [새 글] 1개가 있습니다.

당신의 유일한 임무는 "[새 글]이 [과거 발행 글 목록] 중 하나와 모순되는가"만 판단하는 것입니다.
과거 발행 글 목록 항목들끼리 서로 모순되더라도(예: 과거 글 2개가 이미 서로 다른 수치를 담고
있더라도) 그건 이번 판단 대상이 아니니 완전히 무시하세요. 오직 [새 글]과 과거 글 하나하나를
1대1로 비교해서, [새 글]이 과거 글 중 하나와 "같은 사건·같은 거래·같은 사실"을 언급하면서
가격·날짜·수량·비율 같은 구체적 수치나 사실관계가 달라졌을 때만 모순입니다. 예: 과거 글에
"삼성전자 8만원에 팔았다"가 있고 [새 글]도 똑같이 "삼성전자를 팔았다"는 내용인데 가격이
"25만원"으로 다르면 모순. 소재·주제가 겹치는 것 자체는 문제가 아닙니다.

판단: CONTRADICT 또는 OK 한 단어만 반환.
CONTRADICT면 콜론 뒤에 [새 글]이 과거 글 몇 번과 무엇이 모순되는지 한 줄로 설명.
OK면 그냥 OK만."""


def _consistency_check(text: str, recent_posts: list[dict]) -> tuple[bool, str]:
    """새 글이 과거 발행 글과 같은 사실을 다른 수치로 재서술해 자기모순을 만드는지
    Claude haiku로 검사한다(2026-07-14 신설 — "삼성전자 8만원" 오기재를 나중에
    다른 숫자로 다시 써서 기존 글과 앞뒤가 안 맞게 된 사례 방지).
    반환: (통과: bool, 이유: str)"""
    if not recent_posts:
        return True, ""
    history_str = "\n".join(
        f"{i + 1}. ({p['date']}) {p['text'][:200]}" for i, p in enumerate(recent_posts)
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": f"{CONSISTENCY_CHECK_PROMPT}\n\n[과거 발행 글 목록]\n{history_str}\n\n[새 글]\n{text}",
            }],
        )
        result = response.content[0].text.strip()
        if result.upper().startswith("CONTRADICT"):
            reason = result.split(":", 1)[-1].strip() if ":" in result else result
            logger.warning(f"[consistency] CONTRADICT — {reason}")
            return False, reason
        return True, ""
    except Exception as e:
        logger.warning(f"[consistency] 검사 실패 (통과 처리): {e}")
        return True, ""


def get_current_phase() -> int:
    days = (today_kst() - START_DATE).days
    if days < 90:
        return 1
    elif days < 180:
        return 2
    return 3


# 계절 제한 맵 (content_seeds.json의 "seasonal" 섹션)
_SEASONAL: dict[str, list[int]] = SEEDS.get("seasonal", {})


def _in_season(theme: str) -> bool:
    """해당 소재가 현재 월에 사용 가능한지 확인. 제한 없으면 True."""
    allowed_months = _SEASONAL.get(theme)
    if not allowed_months:
        return True
    return today_kst().month in allowed_months


def _filter_seasonal(themes: list[str]) -> list[str]:
    """시즌 외 소재를 제거. 전부 제외되면 원본 전체 반환(fallback)."""
    filtered = [t for t in themes if _in_season(t)]
    if not filtered:
        logger.debug("계절 필터 후 후보 없음 — 원본 전체 사용")
        return themes
    removed = len(themes) - len(filtered)
    if removed:
        logger.debug(f"계절 필터: {removed}개 소재 제외됨 (현재 {today_kst().month}월)")
    return filtered


def generate_post(slot: str, phase: int | None = None) -> tuple[str, str, dict]:
    if phase is None:
        phase = get_current_phase()

    content_type = SLOT_MATRIX[slot][phase]

    # 최근 사용 현황 로드 (다양성 보장)
    recent_usage = get_recent_usage(days=14)

    # 다양성 모니터 보정 파일 로드 (없으면 빈 dict)
    _override: dict = {}
    _override_path = DATA_DIR / "diversity_override.json"
    if _override_path.exists():
        try:
            with open(_override_path, "r", encoding="utf-8") as _f:
                _override = json.load(_f)
        except Exception:
            pass
    _theme_excl  = set(_override.get("theme_exclusions", []))
    # 하드 쿨다운: 최근 THEME_COOLDOWN_DAYS(60일) 내 실제로 사용된 소재는 override 파일과
    # 무관하게 항상 후보에서 제외한다 (14일 소프트 패널티만으로는 14일 초과 시 재선택되는
    # 문제가 있었음 — 위 THEME_COOLDOWN_DAYS 주석 참고).
    _theme_cooldown_usage = get_recent_usage(days=THEME_COOLDOWN_DAYS)["themes"]
    _theme_excl |= {t for t, c in _theme_cooldown_usage.items() if c > 0}

    # content_type_exclusions 강제 적용 — 일상글 비율 초과 시 대체 타입으로 교체
    _ct_excl = set(_override.get("content_type_exclusions", []))
    if content_type in _ct_excl:
        _slot_fallback = {
            "morning": "economy_news",
            "noon":    "finance",
            "evening": "book_insight",
        }
        _alt = _slot_fallback.get(slot, "economy_news")
        if _alt in _ct_excl:
            # fallback도 제외 목록이면 제외되지 않은 타입 중 랜덤 선택
            _safe = [ct for ct in CONTENT_CONFIG if ct not in _ct_excl]
            _alt = random.choice(_safe) if _safe else content_type
        logger.info(f"[다양성] content_type '{content_type}' → '{_alt}' (일상글 비율 초과 보정)")
        content_type = _alt

    cfg = CONTENT_CONFIG[content_type]

    # economy_news는 코스피·삼성전자·환율 같은 실제 시장 수치를 언급할 여지가 커서,
    # LLM이 학습 시점 기준 숫자를 지어내는 걸 막기 위해 실시간 스냅샷을 프롬프트에
    # 주입하고 생성 후 대조 검증한다(2026-07-05 "삼성전자 8만원" 오발행 사례 대응).
    market_snapshot = market_data.get_market_snapshot() if content_type == "economy_news" else {}
    market_data_prompt_block = (
        market_data.format_snapshot_for_prompt(market_snapshot) if content_type == "economy_news" else ""
    )

    # 소재(theme) 선택 — 트렌드 혼합 → 시즌 필터 → override 중복 제거 → 다양성 가중치
    trend_themes = get_trend_themes(content_type)
    combined_pool = trend_themes * 2 + cfg["themes"]
    themes_pool = _filter_seasonal(combined_pool)
    deduped_pool = [t for t in themes_pool if t not in _theme_excl]
    if not deduped_pool:
        logger.debug("override 중복 제거 후 후보 없음 — 전체 풀 사용")
        deduped_pool = themes_pool
    theme = _diversity_pick(deduped_pool, recent_usage["themes"])
    is_trend = theme in trend_themes

    # 템플릿 선택 — 최근 자주 쓴 템플릿은 낮은 확률로
    template_key = _diversity_pick(cfg["templates"], recent_usage["templates"])
    template = SEEDS["templates"][template_key]

    # 성과 패턴 로드 → 가중치 보정
    patterns = get_top_patterns()
    boosts = build_weight_boosts(patterns)

    # 어투 선택 — 성과 보정(1.5배) + 다양성 패널티 동시 적용
    tone_items = [
        {**t, "weight": t["weight"] * (1.5 if t["name"] == boosts.get("tone") else 1)}
        for t in TONES
    ]
    tone = _diversity_pick(tone_items, recent_usage["tones"])

    # 마무리 유형 선택 — 성과 보정 + 다양성 패널티 동시 적용
    ending_items = [
        {**e, "weight": e["weight"] * (1.5 if e["label"] == boosts.get("ending_style") else 1)}
        for e in SEEDS["endings"].values()
    ]
    ending_style = _diversity_pick(ending_items, recent_usage["endings"], weight_key="label")

    length = _weighted_pick(LENGTHS)

    # 마무리 유형별 지시문 구성
    ending_rule = ending_style["rule"]
    if ending_style["label"] == "질문형":
        examples = ending_style["examples"]
        # content_type에 맞는 예시 카테고리 선택
        if content_type in ("agro", "agro_finance"):
            pool = examples["agro"]
        elif content_type in ("finance", "finance_insurance"):
            pool = examples["finance"]
        else:
            pool = examples["insight"]
        ending_rule += f" 예시 참고(그대로 쓰지 말고 소재에 맞게 변형): \"{random.choice(pool)}\""

    # 경력 표현 풀 — "증권"류 표현은 보험 진단 맥락(finance_insurance/diagnosis/job_insight)에서만 사용.
    # 그 외 콘텐츠(경제뉴스·재테크·일상 등)에서는 "증권"이 주식 용어로 오독될 수 있어 제외하고,
    # 실제 쓰일 때도 "보험 증권"으로 명시해 의미를 분명히 한다.
    _career_phrases = [
        "13년 보니까 ~",
        "현장에서 보면 ~",
        "고객 수백 명 보고 나서야 알았는데 ~",
        "오래 하다 보니 ~",
        "이 일 하면서 ~",
        "설계사로 13년 있다 보니 ~",
        "상담하다 보면 ~",
        "실무에서 보면 ~",
        "경험상 ~",
        "수년 지나고 보니 ~",
        "그동안 봐온 걸로는 ~",
        "일하면서 느낀 건데 ~",
    ]
    if content_type in ("finance_insurance", "diagnosis", "job_insight"):
        _career_phrases.append("보험 증권 수천 장 들여다보니 ~")
    _career_phrases_str = "\n".join(f"  ▸ \"{p}\"" for p in _career_phrases)

    recent = get_recent_topics(days=7)
    recent_str = "\n".join(f"- {t}" for t in recent[-10:]) if recent else "없음"
    performance_str = format_for_prompt(patterns)
    persona_str = get_persona_summary()

    # 최근 2주 소재·템플릿 사용 빈도 요약 (다양성 힌트로 Claude에게 전달)
    theme_usage  = recent_usage["themes"]
    tmpl_usage   = recent_usage["templates"]
    ending_usage = recent_usage["endings"]
    theme_usage_str = (
        "\n".join(f"  - {t} ({c}회)" for t, c in sorted(theme_usage.items(), key=lambda x: -x[1])[:8])
        if theme_usage else "없음"
    )
    tmpl_usage_str = (
        ", ".join(f"{t}({c}회)" for t, c in sorted(tmpl_usage.items(), key=lambda x: -x[1]))
        if tmpl_usage else "없음"
    )
    ending_usage_str = (
        ", ".join(f"{e}({c}회)" for e, c in sorted(ending_usage.items(), key=lambda x: -x[1]))
        if ending_usage else "없음"
    )

    system_prompt = f"""당신은 48세 재무상담사입니다. 쓰레드(Threads) SNS에 올릴 글을 씁니다.

[이 사람이 누구인가]
13년차 GA 소속 보험설계사. 고2 아들(첫째), 중1 딸(둘째)을 둔 가장.

지금 그는 선택의 언어로 말하는 사람이다.
"돈이 없어서 못 한다"가 아니라 "그건 지금 우선순위가 아니다"고 말한다.
"친구가 부럽다"가 아니라 "저 친구는 저 방식으로 살고 있구나"고 관찰한다.

13년 동안 다른 사람 돈을 들여다봤더니 내 돈이 보이기 시작했다.
골프채 174만 원 사서 12번 치고 창고에 넣은 전력이 있다. 그 실패들이 지금의 그를 만들었다.
지금은 후배한테 밥을 산다. 계산할 때 머뭇거리지 않는다.
동창 모임에서 돈 얘기가 나오면 한 마디 하고 — 끝나면 두세 명이 조용히 번호를 물어본다.
영업하지 않는다. 13년 동안 신뢰로 일한다.

[말투]
- 결론부터. 숫자로. 설교 없이.
- "~해야 합니다" 식의 설교체 금지. 대신 경력을 녹인 관찰 어투로.
  경력 표현은 아래 풀에서 매번 다르게 골라 쓰되, 한 글에 한 번만 쓴다.
{_career_phrases_str}
  "보험 증권"(진단 관련 글에서만 등장)과 "13년"류 표현이 최근 글에서 반복되지 않도록
  매번 다른 표현을 고른다. 절대 같은 글에서 두 번 이상 반복하지 않는다.
- 자기 약점도 말하되, 현재의 자신이 더 중요하다. 가볍게 지나간다.
- 공감할 때도 담담하다. "나도 그랬어"가 아니라 "그 나이 되면 다 그런 시기 있어."
{BRAND_SAFETY_PROMPT}

[글쓰기 규칙]
1. {length['instruction']}
2. 줄바꿈 자주 — 2~3문장마다 한 줄씩
3. 숫자는 구체적으로 (월 300만원 ❌ / 월 317만원 ✅) — 단, 코스피·삼성전자·환율처럼
   실제로 존재하는 시장 지수·종목·환율의 "현재 시세"를 언급할 때는 절대 임의로
   숫자를 지어내지 마세요. 아래 [실시간 시장 데이터] 섹션에 값이 주어진 것만
   그 숫자로 쓰고, 없는 종목/지수는 구체적 숫자 없이 방향성만 말하세요.
4. 결론 먼저, 근거 뒤에
5. 과장 표현 절대 금지 (최고, 완벽, 꼭, 반드시)
6. 마무리: {ending_rule}
7. 이모지 사용 안 함
8. 어투: {tone['instruction']}
{"[실시간 시장 데이터]" + chr(10) + market_data_prompt_block if market_data_prompt_block else ""}

[운영 지침]
{PHASE_INSTRUCTIONS[phase]}

[현재 슬롯·콘텐츠 유형]
{slot} 슬롯 / {cfg['label']}

[콘텐츠 지침]
{cfg['guide']}

[최근 발행 글 주제 (반복 금지 — 이 주제들과 겹치는 글 금지)]
{recent_str}

[최근 2주 소재·템플릿 사용 현황 (다양성 확보 참고)]
소재 사용 빈도:
{theme_usage_str}
템플릿 빈도: {tmpl_usage_str}
마무리 빈도: {ending_usage_str}
→ 위에서 자주 나온 소재·구조·마무리와 최대한 다른 방향으로 작성하세요.

[성과 기반 개선 힌트]
{performance_str}

{persona_str}

글 내용만 출력하세요. 제목, 태그, 설명 없이 본문만."""

    trend_note = (
        "※ 이 소재는 오늘 실시간 뉴스에서 추출한 트렌드 소재입니다. "
        "현재 사람들이 관심 갖는 이슈임을 반영해 시의성 있게 작성하되, "
        "페르소나(13년차 보험설계사 관찰자 시점)는 유지하세요."
        if is_trend else ""
    )

    user_prompt = f"""소재: {theme}
템플릿: {template['name']} — {template['structure']}
마무리 스타일: {ending_style['label']} / 어투: {tone['name']} / 길이: {length['name']}
{trend_note}
위 조건으로 오늘 {slot} 슬롯에 올릴 쓰레드 글 1개를 작성하세요."""

    # 과거 발행 글 전문 — 모순 검증용(2026-07-14 신설). 매 시도마다 새로 조회할
    # 필요 없이 이번 generate_post() 호출 동안은 고정된 과거 이력이면 충분하다.
    recent_post_texts = get_recent_post_texts(days=90)

    for attempt in range(3):
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=700,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()

        # 브랜드 안전 검증 — 실패 시 최대 2회 재생성
        passed, fail_reason = _brand_safety_check(text)
        violation_kind = "brand_safety" if not passed else None

        # economy_news 실시간 수치 검증 — 실제 시세와 동떨어진 숫자를 지어냈으면
        # 브랜드 안전과 별개로 재생성 대상(2026-07-05 "삼성전자 8만원" 사고 재발 방지)
        market_violations = (
            market_data.check_market_numbers(text, market_snapshot)
            if content_type == "economy_news" else []
        )
        if market_violations:
            passed = False
            fail_reason = "실시간 시세 불일치: " + " / ".join(market_violations)
            violation_kind = "market_data"

        # 과거 발행 글과의 자기모순 검증 — 위 두 검사를 통과했을 때만 수행(비용 절약).
        # 같은 사건(예: "삼성전자 팔았다")을 과거와 다른 수치로 다시 서술하면 잡아낸다.
        consistency_reason = ""
        if passed:
            c_passed, consistency_reason = _consistency_check(text, recent_post_texts)
            if not c_passed:
                passed = False
                fail_reason = "과거 발행 글과 모순: " + consistency_reason
                violation_kind = "consistency"

        if passed:
            break
        if attempt < 2:
            logger.warning(f"[content_check] 재생성 시도 {attempt + 1}/2 — {fail_reason}")
            retry_notes = {
                "brand_safety": "재정 결핍·열등감·동창 비교 소재를 완전히 제거하고 다시 작성하세요.",
                "market_data": (
                    "코스피·삼성전자·환율 등 실제 시세는 위 [실시간 시장 데이터]에 주어진 값만 "
                    "그대로 쓰거나, 없는 종목은 구체적 숫자 없이 방향성만 서술하세요."
                ),
                "consistency": (
                    "위에서 지적된 과거 글과 겹치는 사건/사실을 다른 소재로 바꾸거나, 같은 사건을 "
                    "다시 쓰려면 과거 글과 동일한 구체적 수치를 그대로 사용하세요."
                ),
            }
            user_prompt_retry = (
                f"{user_prompt}\n\n"
                f"⚠️ 이전 생성글 거부됨: {fail_reason}\n"
                f"{retry_notes.get(violation_kind, '')}"
            )
            user_prompt = user_prompt_retry
        else:
            logger.error(f"[content_check] 3회 시도 모두 실패. 마지막 글로 발행. 수동 확인 필요.")

    meta = {
        "tone": tone["name"],
        "ending_style": ending_style["label"],
        "template": template_key,
        "theme": theme,
    }
    meta["is_trend"] = is_trend
    source_label = "트렌드" if is_trend else "seeds"
    logger.info(
        f"Generated {len(text)}자 ({content_type}) [{source_label}] "
        f"tone={meta['tone']} ending={meta['ending_style']} "
        f"template={template_key} theme={theme[:25]}"
    )
    return text, content_type, meta
