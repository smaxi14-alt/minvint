"""카드뉴스 세션 진입점 — 사람이 Claude Code 세션에서 "카드뉴스 큐 확인해줘"
라고 요청하면 아래 함수들을 이 순서로 호출한다.

1. get_next_item() — 오늘 선정된 큐 항목(status=awaiting_image) 로드
2. (세션 내 Claude Code/design 에이전트가 _context/design-style-guide.md →
   brand-guidelines.md 로딩 후 슬라이드별 mcp__nanobanana__generate_image
   (model:"pro")를 직접 호출 — 이 모듈은 이미지를 생성하지 않는다, CLAUDE.md
   고정 규칙. 이 단계는 세션 기반이라 QA 생략 여부와 무관하게 항상 필요하다)
3. mark_images_ready(item_id, image_paths, caption, hashtags)
4-A. [기본] qa 에이전트가 _templates/qa-checklist.md 기준으로 검수 →
   mark_qa_passed(item_id) 또는 mark_qa_failed(item_id, reason)
4-B. [2026-07-16 사용자 명시적 결정] "앞으로는 사람 검수 없이 자동 발행해줘,
   내가 사후에 검증할게" — mark_ready_to_publish(item_id)로 QA 패스를
   생략하고 바로 발행 가능 상태로 전환. 이 결정은 최초의 실제 라이브 발행
   1건이 3차 QA(2회 FAIL: 원문에 없는 통계 삽입, 디자인 가이드 위반)를
   거쳐 실제로 문제를 걸러낸 뒤에 사용자가 내린 것이므로, 이 함수를 호출하는
   쪽(오케스트레이터)이 그 리스크를 사용자에게 다시 상기시킬 필요는 없다.
   mark_qa_passed()는 남겨뒀으니 특정 건만 사람 검수를 다시 받고 싶으면
   그쪽을 대신 호출하면 된다.
5. publish(item_id) 호출 — poster.py로 이미지를 GitHub에 공개 호스팅한 뒤
   Buffer IG 캐러셀 발행 → tracker.py 기록 → status: published
"""
import json
import logging
from pathlib import Path
from datetime import date

from config import DATA_DIR
import selector
import poster
import tracker

logger = logging.getLogger(__name__)

QUEUE_DIR = DATA_DIR / "cardnews_queue"


def _path_for(item_id: str) -> Path:
    return QUEUE_DIR / f"{item_id}.json"


def _load(item_id: str) -> dict:
    return json.loads(_path_for(item_id).read_text(encoding="utf-8"))


def _save(item: dict) -> None:
    _path_for(item["id"]).write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def get_next_item() -> dict | None:
    """오늘 처리할 큐 항목을 가져온다. awaiting_image 상태가 여러 건이면
    가장 오래된 것부터 (daily_scanner.py는 하루 1건만 선정하므로 보통
    0~1건이지만, 큐 적체 시 여러 건이 쌓여 있을 수 있음)."""
    items = selector.get_awaiting_image_items()
    if not items:
        return None
    return min(items, key=lambda i: i["created_at"])


def mark_images_ready(item_id: str, image_paths: list[str], caption: str, hashtags: list[str]) -> dict:
    """design 에이전트가 슬라이드 이미지를 전부 생성해 콘텐츠/[시리즈명]/
    폴더에 저장한 뒤 호출한다."""
    item = _load(item_id)
    item["image_paths"] = [str(p) for p in image_paths]
    item["caption"] = caption
    if len(hashtags) > 5:
        logger.warning(f"[cardnews] 해시태그 {len(hashtags)}개 → 5개로 절단 (2025.12 Meta 제한)")
    item["hashtags"] = hashtags[:5]
    item["status"] = "ready_for_qa"
    _save(item)
    logger.info(f"[cardnews] {item_id}: 이미지 {len(image_paths)}장 준비 완료 → QA 대기")
    return item


def mark_qa_passed(item_id: str) -> dict:
    item = _load(item_id)
    item["status"] = "approved"
    _save(item)
    logger.info(f"[cardnews] {item_id}: QA 통과")
    return item


def mark_qa_failed(item_id: str, reason: str) -> dict:
    item = _load(item_id)
    item["status"] = "revision_needed"
    item["qa_fail_reason"] = reason
    _save(item)
    logger.warning(f"[cardnews] {item_id}: QA 실패 — {reason}")
    return item


def mark_ready_to_publish(item_id: str) -> dict:
    """QA 에이전트 검수 없이 바로 발행 가능 상태로 전환한다(2026-07-16
    사용자 명시적 결정 — "앞으로는 사람 검수 없이 자동 발행해줘, 내가
    사후에 검증할게"). publish()는 여전히 approved 상태만 받아들이므로
    이 함수가 그 상태 전환을 대신 수행한다 — 상태 머신 자체는 그대로
    유지, QA 단계만 생략."""
    item = _load(item_id)
    item["status"] = "approved"
    item["qa_skipped"] = True
    _save(item)
    logger.info(f"[cardnews] {item_id}: QA 생략(사용자 요청) — 발행 대기 상태로 전환")
    return item


def publish(item_id: str) -> str:
    """QA 통과(approved) 상태에서만 호출 가능 — mark_qa_passed() 또는
    mark_ready_to_publish() 둘 중 하나를 거쳐야 한다. 이미지를 GitHub에
    공개 커밋+푸시한 뒤 Buffer IG 캐러셀 발행 → tracker 기록 →
    status: published. 발행 자체가 실패하면 publish_failed로 남기고
    예외를 다시 던진다(카드뉴스는 실패해도 강행 발행하지 않는다)."""
    item = _load(item_id)
    if item["status"] != "approved":
        raise ValueError(
            f"{item_id}는 approved 상태가 아님(현재: {item['status']}) — "
            f"QA 통과(mark_qa_passed) 또는 QA 생략(mark_ready_to_publish) 먼저 필요"
        )

    caption_with_tags = item["caption"].rstrip() + "\n\n" + " ".join(f"#{h}" for h in item["hashtags"])
    image_paths = [Path(p) for p in item["image_paths"]]
    commit_message = (
        f"card: {item['source_ref'].get('title', item_id)} (인스타그램)\n\n"
        f"자동 변환 파이프라인 산출물, queue_id={item_id}, {date.today().isoformat()}"
    )

    try:
        buffer_post_id = poster.publish_cardnews(caption_with_tags, image_paths, commit_message)
    except Exception:
        item["status"] = "publish_failed"
        _save(item)
        logger.error(
            f"[cardnews] {item_id}: 발행 실패 — publish_failed로 기록, 다음 세션에서 재시도 가능",
            exc_info=True,
        )
        raise

    item["status"] = "published"
    item["buffer_post_id"] = buffer_post_id
    _save(item)
    tracker.log_post(item_id, item["source_type"], item["persona"], len(item["slides"]), buffer_post_id)
    logger.info(f"[cardnews] {item_id}: 발행 완료 (buffer_post_id={buffer_post_id})")
    return buffer_post_id
