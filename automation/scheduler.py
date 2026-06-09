"""
쓰레드 자동 발행 스케줄러
실행: python scheduler.py
종료: Ctrl+C
"""
import logging
import sys
from pathlib import Path

# 로그 디렉토리 먼저 생성
Path("logs").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/scheduler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    ANTHROPIC_API_KEY, BUFFER_ACCESS_TOKEN, BUFFER_CHANNEL_ID,
    MORNING_HOUR, MORNING_MINUTE,
    NOON_HOUR, NOON_MINUTE,
    EVENING_HOUR, EVENING_MINUTE,
    INSTAGRAM_HOUR, INSTAGRAM_MINUTE,
    START_DATE,
)
from content_generator import generate_post, get_current_phase
from buffer_poster import post_to_buffer
from content_tracker import log_post, was_posted_today
from persona_manager import update_facts_from_post
from diversity_monitor import run_once as diversity_check

try:
    from instagram_pipeline import run_instagram_pipeline
    _instagram_available = True
except ImportError:
    _instagram_available = False
    logger.warning("instagram_pipeline 모듈 없음 — Instagram 슬롯 비활성화")


def _check_config():
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not BUFFER_ACCESS_TOKEN:
        missing.append("BUFFER_ACCESS_TOKEN")
    if not BUFFER_CHANNEL_ID:
        missing.append("BUFFER_CHANNEL_ID")
    if missing:
        logger.error(f"필수 환경변수 누락: {', '.join(missing)}")
        logger.error("1) python get_buffer_token.py 실행 후 .env에 토큰 추가")
        logger.error("2) BUFFER_PROFILE_ID도 .env에 추가 필요")
        sys.exit(1)


def run_slot(slot: str):
    if was_posted_today(slot):
        logger.info(f"[{slot}] 오늘 이미 발행됨. 건너뜀.")
        return

    phase = get_current_phase()
    logger.info(f"[{slot}] Phase {phase} 시작 — 글 생성 중...")

    try:
        text, content_type, meta = generate_post(slot, phase)
        logger.info(
            f"[{slot}] 생성 완료 ({len(text)}자) "
            f"tone={meta.get('tone')} template={meta.get('template')} "
            f"theme={str(meta.get('theme',''))[:20]}\n{'-'*40}\n{text}\n{'-'*40}"
        )

        post_id = post_to_buffer(text)
        log_post(slot, content_type, text, post_id, phase, meta)
        logger.info(f"[{slot}] 발행 성공. post_id={post_id}")

        # 발행 글에서 새 페르소나 사실 추출 → persona_store.json 업데이트
        update_facts_from_post(text)

        # 발행 직후 다양성 감시 — override 파일 갱신 (silent)
        diversity_check(silent=True)

    except Exception as e:
        logger.error(f"[{slot}] 실패: {e}", exc_info=True)


def morning():
    run_slot("morning")


def noon():
    run_slot("noon")


def evening():
    run_slot("evening")


def instagram():
    if not _instagram_available:
        logger.info("[instagram] 모듈 없음 — 건너뜀")
        return
    run_instagram_pipeline()


def daily_diversity_report():
    logger.info("[다양성] 일일 보고 시작")
    diversity_check(silent=False)


if __name__ == "__main__":
    _check_config()

    phase = get_current_phase()
    days_elapsed = (
        __import__("datetime").date.today() - START_DATE
    ).days

    logger.info("=" * 50)
    logger.info("쓰레드 자동 발행 스케줄러 시작")
    logger.info(f"운영 시작일: {START_DATE}  |  경과: {days_elapsed}일  |  현재 Phase: {phase}")
    logger.info(
        f"스케줄: 모닝 {MORNING_HOUR}:{MORNING_MINUTE:02d} | "
        f"점심 {NOON_HOUR}:{NOON_MINUTE:02d} | "
        f"저녁 {EVENING_HOUR}:{EVENING_MINUTE:02d} | "
        f"Instagram 카드뉴스 {INSTAGRAM_HOUR}:{INSTAGRAM_MINUTE:02d} (KST)"
    )
    logger.info("종료: Ctrl+C")
    logger.info("=" * 50)

    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        morning,
        CronTrigger(hour=MORNING_HOUR, minute=MORNING_MINUTE),
        id="morning",
        name="Morning Post",
    )
    scheduler.add_job(
        noon,
        CronTrigger(hour=NOON_HOUR, minute=NOON_MINUTE),
        id="noon",
        name="Noon Post",
    )
    scheduler.add_job(
        evening,
        CronTrigger(hour=EVENING_HOUR, minute=EVENING_MINUTE),
        id="evening",
        name="Evening Post",
    )
    scheduler.add_job(
        instagram,
        CronTrigger(hour=INSTAGRAM_HOUR, minute=INSTAGRAM_MINUTE),
        id="instagram",
        name="Instagram Card News",
    )
    scheduler.add_job(
        daily_diversity_report,
        CronTrigger(hour=0, minute=5),
        id="diversity_report",
        name="Daily Diversity Report",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료.")
