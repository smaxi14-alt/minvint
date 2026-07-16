"""셀럽 트렌드 블로그 — STAGE 5: 자동 검증 게이트 (발행 전 필수).

blog_safety_check.py(보험 블로그)와 동일한 설계: 사람 판단이 아니라 규칙으로
걸러지는 항목만 검사한다. 완전 자동 운영이므로 사전 사람 검수는 없고, 여기서
하나라도 실패하면 발행하지 않고 blocked 처리한다(사용자 개발 지시서 STAGE 5).
"""
import re

import celeb_db as db
from config import CELEB_MAX_DAILY_POSTS

NEGATIVE_KEYWORDS = [
    "이혼설", "열애설", "결별설", "불화설", "논란", "저격", "폭로", "루머",
    "의혹", "구설수", "악플", "비방", "고소", "고발", "학폭",
]

CLICKBAIT_PATTERNS = [
    "충격", "경악", "이럴수가", "실화냐", "발칵", "결국", "역대급 논란",
    "완전 몰락", "종결", "레전드 폭로",
]


def check_negative_content(body: str, title: str) -> list[str]:
    violations = []
    for kw in NEGATIVE_KEYWORDS:
        if kw in body or kw in title:
            violations.append(f"부정/루머성 키워드 검출: '{kw}'")
    return violations


def check_clickbait(title: str) -> list[str]:
    violations = []
    for kw in CLICKBAIT_PATTERNS:
        if kw in title:
            violations.append(f"낚시성 제목 패턴 검출: '{kw}'")
    return violations


def check_no_meta_leakage(body: str) -> list[str]:
    """2026-07-15 사용자 피드백 — "[미확인]", "(출처 N개)" 같은 내부 자료용
    메타 표기가 발행된 본문에 그대로 노출되는 사고가 있었다(리한나 발행분에서
    실측). celeb_generator.py 프롬프트에서 이 표기를 쓰지 말라고 지시하지만,
    LLM 출력이 지시를 어길 가능성에 대비해 여기서도 한 번 더 걸러낸다 —
    예전엔 반대로 "[미확인] 태그가 없으면 위반"이었는데, 그 요구사항 자체를
    사용자가 취소했다."""
    violations = []
    if "[미확인]" in body:
        violations.append("본문에 '[미확인]' 메타 태그 노출 (헤징은 어투로만 표현해야 함)")
    if re.search(r"\(출처\s*\d+개\)", body):
        violations.append("본문에 '(출처 N개)' 메타 표기 노출")
    return violations


def check_daily_limit() -> list[str]:
    count = db.count_published_today()
    if count >= CELEB_MAX_DAILY_POSTS:
        return [f"일일 발행 상한 도달: 오늘 {count}/{CELEB_MAX_DAILY_POSTS}건"]
    return []


def check_asset_types(assets: list[dict]) -> list[str]:
    """구조적으로 항상 통과한다 — download_reupload 타입을 만드는 코드 경로가
    아예 없으므로(celeb_db.add_asset()이 ValueError로 차단). 그래도 방어적으로
    한 번 더 확인한다."""
    allowed = {"embed", "official", "context", "textcard", "stock", "downloaded_photo"}
    violations = []
    for a in assets:
        if a.get("type") not in allowed:
            violations.append(f"허용되지 않은 자산 타입: {a.get('type')}")
    return violations


def check_hero_placement(assets: list[dict], body_markdown: str) -> list[str]:
    """대표이미지(hero, celeb_assets._mark_hero() 참조)가 실제로 본문에
    배치됐고 첫 번째 이미지 마커인지 확인한다(2026-07-15 신설). run_all_checks()
    의 발행 차단 판정에는 포함하지 않는다 — run_celeb_pipeline.py의
    _promote_hero_asset()이 이미 결정론적으로 순서를 보장하므로, 이 체크가
    실제로 잡아내는 건 "LLM이 hero 자산을 본문에 아예 누락시킨" 극단적
    케이스뿐이다(promotion으로도 고칠 수 없는 유일한 경우). 호출자가 로그만
    남기는 용도."""
    hero_idx = next((i for i, a in enumerate(assets) if a.get("is_hero")), None)
    if hero_idx is None:
        return []
    hero_marker = f"[[ASSET:{hero_idx}]]"
    if hero_marker not in body_markdown:
        return [f"대표이미지(ASSET:{hero_idx})가 본문에 배치되지 않음"]
    first_marker = re.search(r"\[\[ASSET:(\d+)\]\]", body_markdown)
    if first_marker and int(first_marker.group(1)) != hero_idx:
        return [f"대표이미지(ASSET:{hero_idx})가 첫 번째 이미지가 아님(첫 마커: ASSET:{first_marker.group(1)})"]
    return []


def run_all_checks(draft: dict, facts: list[dict], assets: list[dict], dry_run: bool = False) -> list[str]:
    """draft: {"titles": [...], "body_markdown": ..., "needs_more_facts": bool} (celeb_generator.py 출력)
    빈 리스트면 발행 가능(ready_to_publish). 비어있지 않으면 blocked.

    dry_run=True면 일일 발행 상한 검사를 건너뛴다 — dry-run은 실제 발행이
    아니라 파이프라인 검증용이라 count_published_today()에 반영되지 않는데,
    이 검사가 dry_run 여부를 몰라서 테스트 자체가 상한에 막히는 문제가
    있었다(2026-07-14 실측 발견)."""
    violations = []

    if draft.get("needs_more_facts"):
        violations.append("needs_more_facts=True — 팩트 부족으로 생성 단계에서 스스로 보류 판정")

    title = draft["titles"][0] if draft.get("titles") else ""
    body = draft.get("body_markdown", "")

    violations += check_negative_content(body, title)
    violations += check_clickbait(title)
    violations += check_no_meta_leakage(body)
    violations += check_asset_types(assets)
    if not dry_run:
        violations += check_daily_limit()

    return violations


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db.init_db()
    sample_draft = {
        "titles": ["강재준, 요요로 100kg 돌파 후 다시 다이어트 도전"],
        "body_markdown": "강재준이 다이어트 후 요요를 겪었다고 밝혔어요. 정확한 몸무게는 "
                          "아직 확인되지 않았다고 하네요.",
        "needs_more_facts": False,
    }
    sample_facts = [{"status": "unconfirmed"}]
    sample_assets = [{"type": "textcard"}]
    result = run_all_checks(sample_draft, sample_facts, sample_assets)
    print("위반 사항:", result if result else "없음 (통과)")
