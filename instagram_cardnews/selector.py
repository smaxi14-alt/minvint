"""카드뉴스 큐 후보 스코어링 + 주 3~4회 게이팅.

2026 인스타그램 리서치상 매일 발행하면 과발행이라 주 3~4회가 권장선이다.
매일 저녁 이 모듈이 그날까지 누적된 pending 후보 중 최고 점수 1건만 골라
status를 awaiting_image로 바꾸고 나머지는 큐에 그대로 둔다(다음 날 재평가
대상, 무한 방치 방지를 위해 7일 지나면 expired 처리).
"""
import json
import logging
from datetime import date
from pathlib import Path

from config import DATA_DIR
import tracker

logger = logging.getLogger(__name__)

QUEUE_DIR = DATA_DIR / "cardnews_queue"
MAX_PER_WEEK = 4
EXPIRE_AFTER_DAYS = 7


def _load_all() -> list[dict]:
    items = []
    for path in QUEUE_DIR.glob("*.json"):
        try:
            items.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except Exception as e:
            logger.warning(f"[cardnews] 큐 파일 파싱 실패(스킵): {path.name} — {e}")
    return items


def _save(path: Path, item: dict) -> None:
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def _age_days(item: dict) -> int:
    created = date.fromisoformat(item["created_at"][:10])
    return date.today().toordinal() - created.toordinal()


def score(item: dict) -> float:
    """스코어가 높을수록 우선 선정. 신선도·페르소나 명확성·슬라이드 완성도·
    후킹 숫자 유무를 반영한다."""
    s = 0.0
    s += max(0, 5 - _age_days(item))  # 신선할수록 가점(최대 5일치)
    if item.get("persona") in ("kim_bujang", "park_gwajang"):
        s += 3  # 페르소나 명확 = 훅 카피가 구체적일 가능성 높음
    slide_count = len(item.get("slides", []))
    if 5 <= slide_count <= 8:
        s += 2
    hook = next((sl for sl in item.get("slides", []) if sl.get("role") == "HOOK"), None)
    if hook and hook.get("subtext"):
        s += 3  # 훅 슬라이드에 구체적 숫자가 있으면 가점
    evidence_count = sum(1 for sl in item.get("slides", []) if sl.get("role") == "EVIDENCE")
    s += min(evidence_count, 2)
    return s


def select_daily() -> dict | None:
    """오늘의 카드뉴스 발행 후보 1건을 선정한다. 주간 캡 초과 시 None."""
    recent_dates = tracker.get_recent_publish_dates(days=7)
    if len(recent_dates) >= MAX_PER_WEEK:
        logger.info(f"[cardnews] 주간 발행 캡 도달({len(recent_dates)}/{MAX_PER_WEEK}) — 오늘은 선정 건너뜀")
        return None

    all_items = _load_all()
    pending = []
    for path, item in all_items:
        if item.get("status") != "pending_selection":
            continue
        if _age_days(item) > EXPIRE_AFTER_DAYS:
            item["status"] = "expired"
            _save(path, item)
            logger.info(f"[cardnews] 큐 항목 만료 처리: {item['id']}")
            continue
        pending.append((path, item))

    if not pending:
        logger.info("[cardnews] 선정 가능한 대기 후보 없음")
        return None

    path, best = max(pending, key=lambda pi: score(pi[1]))
    best["status"] = "awaiting_image"
    _save(path, best)
    logger.info(f"[cardnews] 오늘의 카드뉴스 선정: {best['id']} (score={score(best):.1f})")
    return best


def get_awaiting_image_items() -> list[dict]:
    return [item for _, item in _load_all() if item.get("status") == "awaiting_image"]


def get_stale_awaiting_count(days: int = 3) -> int:
    """awaiting_image 상태로 사흘 이상 방치된 항목 수 — 큐 적체 알림 강도
    격상 판단용."""
    count = 0
    for _, item in _load_all():
        if item.get("status") == "awaiting_image" and _age_days(item) >= days:
            count += 1
    return count
