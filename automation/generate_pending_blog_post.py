"""
블로그 글 생성 1단계 — 텍스트·이미지 스펙만 생성해 JSON으로 저장한다.
실제 이미지 생성(nanobanana pro)은 Claude 세션에서 이 JSON의 image_specs를 보고
직접 수행한 뒤, publish_pending_blog_post.py로 최종 발행한다.

Usage: python generate_pending_blog_post.py
"""
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from blog_generator import generate_blog_post
from config import DATA_DIR

PENDING_FILE = DATA_DIR / "pending_blog_post.json"


def main():
    result = generate_blog_post()

    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    logger.info(f"글 생성 완료 — 시리즈: {result['series']}")
    logger.info("제목 후보:\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(result["title_candidates"])))
    logger.info(f"후킹유형={result['hook_type']} 비유분야={result['metaphor_domain']} 시그니처={result['signature'][:30]}")
    logger.info(f"\n{'='*50}\n{result['body']}\n{'='*50}")
    logger.info(f"태그: {result['tags']}")
    logger.info(f"저장 완료: {PENDING_FILE}")
    logger.info(f"이미지 스펙 {len(result['image_specs'])}개:")
    for spec in result["image_specs"]:
        logger.info(f"  [{spec['index']}] ({spec['style']}) {spec['prompt']}")


if __name__ == "__main__":
    main()
