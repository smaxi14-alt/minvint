"""
블로그 글 발행 3단계 — generate_pending_blog_post.py가 만든 JSON을 읽어
(2단계에서 Claude가 nanobanana로 생성해둔) 이미지들과 함께 실제 발행한다.

Usage: python publish_pending_blog_post.py [--dry-run]
이미지 파일은 자동으로 data/blog_images/img_{index}.png 를 찾는다.
없는 인덱스는 건너뛴다 (이미지 없이 그 부분만 생략하고 계속 진행).
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

from naver_blog_poster import post_to_naver_blog
from blog_tracker import log_post
from config import DATA_DIR

PENDING_FILE = DATA_DIR / "pending_blog_post.json"
IMAGES_DIR = DATA_DIR / "blog_images"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="발행 없이 초안만 채워 스크린샷 확인")
    args = parser.parse_args()

    if not PENDING_FILE.exists():
        logger.error(f"{PENDING_FILE} 없음 — 먼저 generate_pending_blog_post.py 실행하세요.")
        return

    with open(PENDING_FILE, "r", encoding="utf-8") as f:
        post = json.load(f)

    image_map = {}
    for spec in post["image_specs"]:
        idx = spec["index"]
        candidate = IMAGES_DIR / f"img_{idx}.png"
        if candidate.exists():
            image_map[idx] = str(candidate)
        else:
            logger.warning(f"이미지 {idx}번({candidate.name}) 없음 — 해당 마커는 건너뜀")

    logger.info(f"이미지 {len(image_map)}/{len(post['image_specs'])}개 확보됨")

    url = post_to_naver_blog(
        post["title"],
        post["body"],
        post["tags"],
        image_map=image_map,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        log_post(
            post["series"],
            post["title"],
            url,
            post["tags"],
            hook_type=post.get("hook_type", ""),
            metaphor_domain=post.get("metaphor_domain", ""),
            signature=post.get("signature", ""),
            elements_used=post.get("elements_used", []),
        )
        logger.info(f"발행 및 로그 기록 완료: {url}")
        PENDING_FILE.unlink()
    else:
        logger.info("dry-run 모드 — 로그 기록 생략. data/naver_dry_run.png 확인 후 --dry-run 없이 재실행하세요.")


if __name__ == "__main__":
    main()
