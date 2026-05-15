"""
GitHub Actions용 단발 발행 스크립트
Usage: python post_once.py --slot [morning|noon|evening]
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

from content_generator import generate_post, get_current_phase
from buffer_poster import post_to_buffer
from content_tracker import log_post


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slot", required=True, choices=["morning", "noon", "evening"]
    )
    args = parser.parse_args()

    phase = get_current_phase()
    logger.info(f"Phase={phase} slot={args.slot} 시작")

    text, content_type, meta = generate_post(args.slot, phase)
    logger.info(f"글 생성 완료 ({len(text)}자) tone={meta['tone']} ending={meta['ending_style']}")
    logger.info(f"\n{'='*45}\n{text}\n{'='*45}")

    post_id = post_to_buffer(text)
    log_post(args.slot, content_type, text, post_id, phase, meta)
    logger.info(f"발행 완료: post_id={post_id}")


if __name__ == "__main__":
    main()
