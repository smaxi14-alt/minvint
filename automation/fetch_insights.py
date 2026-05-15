"""
GitHub Actions 일일 지표 수집 스크립트
- 24시간 이상 된 게시물의 Threads 미디어 ID 탐색
- 인사이트(views, likes, replies, reposts, quotes) 수집 후 로그 저장
Usage: python fetch_insights.py
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))

from config import LOGS_DIR
from threads_analytics import ThreadsAnalytics

LOG_FILE = LOGS_DIR / "posts_log.json"
MIN_AGE_HOURS = 24


def main():
    token = os.getenv("THREADS_ACCESS_TOKEN", "")
    if not token:
        logger.error("THREADS_ACCESS_TOKEN 환경변수 없음")
        sys.exit(1)

    if not LOG_FILE.exists():
        logger.info("posts_log.json 없음 — 종료")
        return

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    analytics = ThreadsAnalytics(token)
    now = datetime.now(timezone.utc)
    updated = 0

    for post in data.get("posts", []):
        if post.get("metrics"):
            continue  # 이미 수집됨

        posted_at = post.get("timestamp", "")
        if not posted_at:
            continue

        try:
            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
            age_hours = (now - dt).total_seconds() / 3600
        except Exception:
            continue

        if age_hours < MIN_AGE_HOURS:
            logger.info(f"24시간 미달 스킵: {post.get('date')} {post.get('slot')} ({age_hours:.1f}h)")
            continue

        # Threads 미디어 ID 확보
        media_id = post.get("threads_media_id")
        if not media_id:
            media_id = analytics.find_media_id(posted_at, post.get("text", ""))
            if media_id:
                post["threads_media_id"] = media_id
                logger.info(f"미디어ID 확보: {media_id}")

        if not media_id:
            logger.warning(f"미디어ID 미발견: {post.get('date')} {post.get('slot')}")
            continue

        # 인사이트 수집
        try:
            metrics = analytics.get_insights(media_id)
            post["metrics"] = metrics
            score = (
                metrics.get("likes", 0) * 3
                + metrics.get("replies", 0) * 2
                + metrics.get("reposts", 0) * 2
                + metrics.get("quotes", 0)
                + metrics.get("views", 0) * 0.01
            )
            post["engagement_score"] = round(score, 2)
            logger.info(
                f"지표 수집 완료: {post.get('date')} {post.get('slot')} "
                f"| views={metrics.get('views',0)} likes={metrics.get('likes',0)} "
                f"replies={metrics.get('replies',0)} score={score:.1f}"
            )
            updated += 1
        except Exception as e:
            logger.warning(f"인사이트 수집 실패 ({media_id}): {e}")

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"완료: {updated}개 지표 업데이트")


if __name__ == "__main__":
    main()
