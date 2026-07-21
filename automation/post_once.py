"""
GitHub Actions용 단발 발행 스크립트
Usage: python post_once.py --slot [morning|noon|evening]
"""
import argparse
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from config import DATA_DIR, today_kst
from content_generator import generate_post, get_current_phase, _brand_safety_check, _consistency_check
from buffer_poster import post_to_buffer
from content_tracker import log_post, was_posted_today, get_recent_post_texts
from persona_manager import update_facts_from_post

QUEUE_FILE = DATA_DIR / "manual_threads_queue.json"


def _load_from_queue(slot: str) -> tuple[str, str, dict] | None:
    """오늘(KST) 날짜+슬롯에 해당하는 사전 작성 글을 큐에서 꺼낸다(2026-07-21 신설
    — Claude Code 세션에서 미리 써서 저장해둔 글을 API 생성 없이 그대로 쓰는 경로).
    저비용 안전장치(brand_safety/consistency, haiku)는 그대로 통과시킨다 — 실패하면
    큐 항목을 버리고 None을 반환해 호출부가 generate_post()로 폴백하게 한다."""
    if not QUEUE_FILE.exists():
        return None
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        queue = json.load(f)

    date_key = today_kst().isoformat()
    day_entries = queue.get(date_key, {})
    entry = day_entries.get(slot)
    if not entry:
        return None

    text = entry["text"]
    passed, reason = _brand_safety_check(text)
    if passed:
        passed, reason = _consistency_check(text, get_recent_post_texts(days=90))
    if not passed:
        logger.warning(f"[큐] {date_key}/{slot} 안전검사 실패 — 큐 버리고 API 생성으로 폴백: {reason}")
        return None

    # 사용한 항목은 큐에서 제거(재사용 방지) — 실패해서 폴백한 항목은 남겨두지 않고
    # 함께 지운다. 사람이 다시 봐야 할 문제라 재시도로 자동 복구될 여지가 없다.
    del day_entries[slot]
    if not day_entries:
        del queue[date_key]
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)

    logger.info(f"[큐] {date_key}/{slot} 사전 작성 글 사용 — API 생성 건너뜀")
    return text, entry["content_type"], entry.get("meta", {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slot", required=True, choices=["morning", "noon", "evening"]
    )
    parser.add_argument(
        "--force", action="store_true", help="오늘 이미 발행된 슬롯도 강제 재실행"
    )
    args = parser.parse_args()

    if not args.force and was_posted_today(args.slot):
        logger.info(f"[{args.slot}] 오늘 이미 발행됨. 건너뜀. (강제 실행: --force)")
        return

    phase = get_current_phase()
    logger.info(f"Phase={phase} slot={args.slot} 시작")

    queued = _load_from_queue(args.slot)
    if queued:
        text, content_type, meta = queued
    else:
        text, content_type, meta = generate_post(args.slot, phase)

    logger.info(f"글 준비 완료 ({len(text)}자) tone={meta.get('tone')} ending={meta.get('ending_style')}")
    logger.info(f"\n{'='*45}\n{text}\n{'='*45}")

    post_id = post_to_buffer(text)
    log_post(args.slot, content_type, text, post_id, phase, meta)
    logger.info(f"발행 완료: post_id={post_id}")

    # 발행 글에서 새 페르소나 사실 추출 → persona_store.json 업데이트
    update_facts_from_post(text)


if __name__ == "__main__":
    main()
