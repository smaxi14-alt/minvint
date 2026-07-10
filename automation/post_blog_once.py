"""
네이버 블로그 1회 발행 스크립트 (Windows 작업 스케줄러용).
Usage: python post_blog_once.py [--dry-run] [--force]
"""
import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from blog_generator import generate_blog_post
from naver_blog_poster import post_to_naver_blog
from blog_tracker import log_post, was_posted_this_week


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="발행 없이 초안만 채워 스크린샷 확인")
    parser.add_argument("--force", action="store_true", help="이번 주 이미 발행됐어도 강제 실행")
    args = parser.parse_args()

    if not args.force and not args.dry_run and was_posted_this_week():
        logger.info("이번 주 이미 블로그 글이 발행됨. 건너뜀. (강제 실행: --force)")
        return

    title, candidates, body, series, tags = generate_blog_post()
    logger.info(f"글 생성 완료 — 시리즈: {series}")
    logger.info("제목 후보:\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates)))
    logger.info(f"\n{'='*50}\n{body}\n{'='*50}")
    logger.info(f"태그: {tags}")

    url = post_to_naver_blog(title, body, tags, dry_run=args.dry_run)

    if not args.dry_run:
        log_post(series, title, url, tags)
        logger.info(f"발행 및 로그 기록 완료: {url}")
    else:
        logger.info("dry-run 모드 — 로그 기록 생략. data/naver_dry_run.png 확인 후 --dry-run 없이 재실행하세요.")


if __name__ == "__main__":
    main()
