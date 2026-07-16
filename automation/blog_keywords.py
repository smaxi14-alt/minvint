"""1일 1글 키워드 로테이션 엔진.

_context/blog-daily-automation-strategy.md §1~3(셔플-소진 방식)을 구현한다.
- Tier A(지역)/B(불안·후회)/C(트리거)의 차원 리스트를 조합(cross-product)해
  키워드 풀을 만든다 — A: 27×10×8=2,160개, B: 22×8=176개, C: 18×6=108개.
- 오늘 어떤 tier를 쓸지는 현재 Phase의 목표 비율 대비 실제 누적 비율이
  가장 부족한 tier를 선택하는 자기보정 방식으로 정한다(요일 고정표보다 견고 —
  스케줄 스킵·연기가 있어도 결국 Phase 목표 비율에 수렴한다).
- 조합 선택은 날짜를 시드로 한 결정적 셔플이라 같은 날 재실행해도 같은 결과가
  나오고(스케줄러 재시도 시 다른 글이 나가는 사고 방지), blog_tracker의
  get_used_combo_keys()로 이미 쓴 조합은 항상 제외한다.
"""
import logging
import random
from datetime import date

from config import START_DATE
from blog_tracker import get_used_combo_keys, get_recent_tiers

logger = logging.getLogger(__name__)

# _context/blog-daily-automation-strategy.md §1 키워드 뱅크 (워크플로우 산출물 그대로 반영)
TIER_A = {
    "locations": [
        "전주 완산구", "전주 덕진구", "전주 효자동", "전주 삼천동", "전주 평화동",
        "전주 서신동", "전주 중화산동", "전주 한옥마을 인근", "전주 서부신시가지",
        "전주 덕진동", "전주 송천동", "전주 인후동", "전주 우아동", "전주 금암동",
        "전주 팔복동", "전주 혁신도시", "전북대 인근", "전주역 인근",
        "전주 고속버스터미널 인근", "전주 코아리베라 상권",
        "익산시", "군산시", "김제시", "정읍시", "완주군", "임실군", "남원시",
    ],
    "topics": [
        "실손보험", "암보험", "종신보험", "연금보험", "자동차보험",
        "화재보험", "사업자보험(화재·배상책임)", "어린이보험", "치아보험", "운전자보험",
    ],
    "actions": [
        "점검", "진단", "리모델링", "상담(판매 권유 없는)",
        "비교", "무료진단", "증권분석", "재설계",
    ],
}

TIER_B = {
    "situations": [
        "실손보험 중복가입", "종신보험 해지 손해", "40대 보험료 과다", "50대 보험료 과다",
        "갱신형 보험료 폭탄", "실손 갈아타기 손해", "보장 공백 방치", "중복 특약 가입",
        "저렴한 보험 해지 후 후회", "보험 리모델링 시기 놓침", "보험료 대비 보장 부족",
        "노후 실손 전환 실기", "10년째 방치된 보험증권", "사업비 과다 보험 가입",
        "무해지환급형 보험 리스크", "변액보험 손실 우려", "중복 특약 정리 안 됨",
        "실손보험 4세대 전환 타이밍", "보험료 연체로 인한 실효 위험",
        "은퇴 후 보험료 부담 증가", "맞벌이 부부 보험 중복", "자녀 독립 후 불필요한 특약",
    ],
    "angles": [
        "확인법", "손해여부 판단법", "줄이는법(해지 말고)", "해지전 체크리스트",
        "비교하는법", "갈아타기 전 확인사항", "유지 vs 해지 판단기준", "증권으로 직접 확인하는 법",
    ],
}

TIER_C = {
    "events": [
        "부모님 치매진단", "암 진단 후 청구", "가장 사망보장 공백", "출산·육아 시작",
        "은퇴·퇴직", "사업자 전환(프리랜서/개인사업자)", "이직·퇴사", "자녀 대학입학·독립",
        "결혼", "내 집 마련(주택구입)", "부모님 간병 시작", "뇌혈관질환 진단",
        "심장질환 진단", "수술 후 보험금 청구", "자녀 출생", "배우자 사망",
        "정년퇴직 후 실손 전환", "갱신 시점 도래(60세·70세)",
    ],
    "angles": [
        "사건 전 준비 체크리스트", "청구 노하우", "보장 공백 진단법",
        "사건 후 증권 재점검법", "세대별 대응 전략", "골든타임(청구기한) 안내",
    ],
}

# _context/blog-daily-automation-strategy.md §2 Phase별 지역:불안:트리거 목표 비율
PHASE_TIER_RATIO = {
    1: {"A": 0.71, "B": 0.21, "C": 0.07},
    2: {"A": 0.71, "B": 0.25, "C": 0.04},
    3: {"A": 0.57, "B": 0.25, "C": 0.18},
    4: {"A": 0.48, "B": 0.36, "C": 0.16},
}
PHASE_START_DAY = {1: 0, 2: 14, 3: 70, 4: 126}


def _current_phase(days_elapsed: int) -> int:
    if days_elapsed < 14:
        return 1
    if days_elapsed < 70:
        return 2
    if days_elapsed < 126:
        return 3
    return 4


def _pick_tier_for_today(days_elapsed: int) -> str:
    """현재 Phase 목표 비율 대비 실제 누적 사용 비율이 가장 부족한 tier를 고른다."""
    phase = _current_phase(days_elapsed)
    target = PHASE_TIER_RATIO[phase]
    days_in_phase = max(days_elapsed - PHASE_START_DAY[phase] + 1, 1)

    recent = get_recent_tiers(days=days_in_phase)
    counts = {"A": recent.count("A"), "B": recent.count("B"), "C": recent.count("C")}
    total = sum(counts.values()) or 1

    deficits = {t: target[t] - counts[t] / total for t in target}
    return max(deficits, key=deficits.get)


def _all_combos_for_tier(tier: str) -> list[dict]:
    if tier == "A":
        return [
            {
                "tier": "A",
                "combo_key": f"{loc}|{topic}|{action}",
                "keyword": f"{loc} {topic} {action}",
                "location": loc, "topic": topic, "action": action,
            }
            for loc in TIER_A["locations"]
            for topic in TIER_A["topics"]
            for action in TIER_A["actions"]
        ]
    if tier == "B":
        return [
            {
                "tier": "B",
                "combo_key": f"{sit}|{angle}",
                "keyword": f"{sit} {angle}",
                "situation": sit, "angle": angle,
            }
            for sit in TIER_B["situations"]
            for angle in TIER_B["angles"]
        ]
    return [
        {
            "tier": "C",
            "combo_key": f"{ev}|{angle}",
            "keyword": f"{ev} {angle}",
            "event": ev, "angle": angle,
        }
        for ev in TIER_C["events"]
        for angle in TIER_C["angles"]
    ]


def _assign_series(combo: dict) -> str:
    """_context/blog-daily-automation-strategy.md §3.4 시리즈 자동 배정 규칙."""
    tier = combo["tier"]

    if tier == "A":
        action, topic = combo["action"], combo["topic"]
        if topic in ("자동차보험", "화재보험", "사업자보험(화재·배상책임)", "치아보험", "운전자보험"):
            return "40대 보험 공백 시리즈"
        if action in ("진단", "무료진단", "증권분석"):
            return "증권 진단 실사례"
        if action == "리모델링" and topic in ("실손보험", "암보험", "종신보험"):
            return "보험료 다이어트"
        return "GA 설계사의 솔직한 말"

    if tier == "B":
        sit = combo["situation"]
        if any(k in sit for k in ("보험료 과다", "줄이는", "보험료 부담")):
            return "보험료 다이어트"
        if any(k in sit for k in ("노후", "은퇴", "맞벌이")):
            return "은퇴 현금흐름"
        return "증권 진단 실사례"

    ev = combo["event"]
    if any(k in ev for k in ("암 진단", "뇌혈관", "심장", "수술")):
        return "증권 진단 실사례"
    if any(k in ev for k in ("은퇴", "퇴직", "전환")):
        return "은퇴 현금흐름"
    if any(k in ev for k in ("사망보장", "출산", "독립", "결혼", "내 집", "치매", "간병")):
        return "40대 보험 공백 시리즈"
    return "증권 진단 실사례"


def pick_today_keyword() -> dict:
    """오늘 발행할 글의 키워드/티어/시리즈를 결정한다.
    반환: {"keyword", "tier", "combo_key", "suggested_series"}

    조합 풀이 모두 소진되면(정상 182일 운영으로는 발생하지 않음 — A는 19.8배,
    B는 3.5배, C는 4.9배 여유) 가장 관대한 폴백으로 전체 풀에서 재선택한다
    (재사용 발행이 무발행보다 낫다는 원칙 — strategy.md §1 참조)."""
    days_elapsed = (date.today() - START_DATE).days
    tier = _pick_tier_for_today(days_elapsed)

    used = get_used_combo_keys()
    pool = _all_combos_for_tier(tier)
    unused = [c for c in pool if c["combo_key"] not in used]

    if not unused:
        logger.warning(f"tier {tier} 조합 완전 소진({len(pool)}개) — 전체 풀에서 재사용")
        unused = pool

    # 날짜+tier 시드의 결정적 셔플 — 같은 날 스케줄러가 재시도해도 같은 조합이 나와
    # 하루 두 번 다른 글이 발행되는 사고를 막는다.
    rng = random.Random(f"{date.today().isoformat()}-{tier}")
    chosen = rng.choice(unused)

    return {
        "keyword": chosen["keyword"],
        "tier": chosen["tier"],
        "combo_key": chosen["combo_key"],
        "suggested_series": _assign_series(chosen),
    }
