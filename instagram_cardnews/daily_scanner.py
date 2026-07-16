"""매일 실행되는 카드뉴스 큐 스캐너 — 쓰레드/블로그 자동화가 이미 발행
완료한 뒤 남기는 로그 파일을 읽기 전용으로 폴링해 새 게시물을 감지하고,
큐에 카드뉴스 대본을 쌓은 뒤, 오늘의 발행 후보 1건을 선정해 알림만 남긴다.
이미지 생성은 하지 않는다(CLAUDE.md 고정 규칙, session.py 참조).

2026-07-16 재설계 — 예전엔 automation/post_once.py, automation/run_daily_blog.py
안에 직접 훅을 심어 발행 직후 호출했지만, 사용자 요청("쓰레드 로직은 지금
완벽하게 구현 중이니 건드리지 말고, 인스타 자동발행은 완전히 다른 폴더로")
에 따라 그 훅을 전부 제거했다. 이 스캐너는 automation/logs/posts_log.json,
automation/logs/blog_log.json을 파일 경로로만 읽는다(파이썬 import 없음) —
저 두 파이프라인의 내부 코드가 어떻게 바뀌든 이 스캐너에는 영향이 없다.

알려진 제약: 블로그 로그(blog_log.json)에는 제목·태그 등 메타데이터만
있고 본문(body)이 저장되지 않는다. automation/data/last_run_post.json에
전체 본문이 있지만, 이 파일은 "가장 최근 실행 결과"만 담는 캐시라 다음 날
블로그가 다시 실행되면 덮어써진다 — 그래서 이 스캐너가 오늘 안에(다음
블로그 실행 전에) 도는 경우에만 본문을 복원할 수 있다. 제목이 일치하지
않으면(이미 덮어써진 경우) 조용히 스킵하고 다음 스캔에서 다시 시도하지
않는다 — 완벽하지 않지만, 로그 구조를 바꾸려면 automation/blog_tracker.py를
수정해야 하는데 이는 "쓰레드/블로그 로직을 건드리지 않는다"는 원칙에
위배되므로 이 정도 제약은 감수한다.
"""
import argparse
import json
import logging
import re
import sys
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from config import THREADS_LOG, BLOG_LOG, BLOG_LAST_RUN_POST
import planner
import selector


def _already_queued(item_id: str) -> bool:
    return planner.queue_item_path(item_id).exists()


def _scan_threads() -> int:
    """posts_log.json은 본문(text) 전체를 저장하므로 완전히 복원 가능."""
    if not THREADS_LOG.exists():
        return 0
    data = json.loads(THREADS_LOG.read_text(encoding="utf-8"))
    created = 0
    for p in data.get("posts", []):
        post_id = p.get("post_id", "")
        text = p.get("text", "")
        if not post_id or not text:
            continue
        item_id_guess = f"{date.fromisoformat(p['date']).strftime('%Y%m%d')}_thread_{post_id}"
        if _already_queued(item_id_guess):
            continue
        meta = {
            "tone": p.get("tone", ""), "ending_style": p.get("ending_style", ""),
            "template": p.get("template", ""), "theme": p.get("theme", ""),
            "is_trend": p.get("is_trend", False),
        }
        item = planner.plan_from_thread(
            text, p.get("content_type", ""), meta, p.get("slot", ""), p.get("phase", 0), post_id,
        )
        if item:
            created += 1
            logger.info(f"[cardnews] 쓰레드 신규 큐 등록: {item['id']}")
    return created


def _scan_blog() -> int:
    """blog_log.json은 메타데이터만 있어, last_run_post.json(당일 최신
    실행분에 한해 유효)과 제목 대조로 본문을 복원한다. 대조 실패 시
    조용히 스킵(다음 블로그 실행이 last_run_post.json을 덮어쓰기 전까지만
    유효한 best-effort)."""
    if not BLOG_LOG.exists():
        return 0
    data = json.loads(BLOG_LOG.read_text(encoding="utf-8"))
    created = 0

    last_run = None
    if BLOG_LAST_RUN_POST.exists():
        try:
            last_run = json.loads(BLOG_LAST_RUN_POST.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[cardnews] last_run_post.json 파싱 실패: {e}")

    for p in data.get("posts", []):
        url = p.get("url", "")
        title = p.get("title", "")
        if not url or not title:
            continue
        slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", title)[:24].strip("-")
        item_id_guess = f"{date.fromisoformat(p['date']).strftime('%Y%m%d')}_blog_{slug}"
        if _already_queued(item_id_guess):
            continue

        if not last_run or last_run.get("title") != title:
            logger.info(
                f"[cardnews] 블로그 본문 복원 불가(캐시가 다른 글로 덮어써짐) — 스킵: {title[:30]}"
            )
            continue

        post = {
            "title": title,
            "body": last_run.get("body", ""),
            "series": p.get("series", last_run.get("series", "")),
            "signature": p.get("signature", last_run.get("signature", "")),
            "keyword": p.get("keyword", last_run.get("keyword", "")),
        }
        item = planner.plan_from_blog(post, url)
        if item:
            created += 1
            logger.info(f"[cardnews] 블로그 신규 큐 등록: {item['id']}")
    return created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="(미사용, 하위호환용)")
    parser.add_argument("--figma-key", default=None, help="(미사용, 하위호환용)")
    args = parser.parse_args()

    t_created = _scan_threads()
    b_created = _scan_blog()
    logger.info(f"[cardnews] 스캔 완료 — 신규 큐 등록: 쓰레드 {t_created}건, 블로그 {b_created}건")

    stale = selector.get_stale_awaiting_count(days=3)
    if stale:
        logger.warning(
            f"[STATUS=QUEUE_STALE] 이미지 대기 중 {stale}건이 3일 이상 방치됨 — "
            f"Claude Code 세션을 열어 처리해주세요"
        )

    picked = selector.select_daily()
    if picked:
        logger.info(
            f"[STATUS=SELECTED] 오늘의 카드뉴스 후보 선정: {picked['id']} "
            f"({picked['source_type']}, {len(picked['slides'])}장, persona={picked['persona']}) — "
            f"Claude Code 세션에서 '카드뉴스 큐 확인해줘'라고 요청하면 이미지 생성부터 진행됩니다"
        )
    else:
        logger.info("[STATUS=NO_CANDIDATE] 오늘은 선정할 카드뉴스 후보가 없습니다(주간 캡 도달 또는 대기 후보 없음)")


if __name__ == "__main__":
    main()
