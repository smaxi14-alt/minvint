"""
네이버 블로그 1회 발행 스크립트 (Windows 작업 스케줄러용).
Usage: python post_blog_once.py [--dry-run] [--force] [--image PATH]

--image: 대표 이미지 파일 경로. 네이버 사진 업로드는 OS 네이티브 대화상자를
자동화하는 방식이라 화면이 보이는 상태여야 한다 — 지정 시 자동으로 headless가
꺼진다. 이미지는 nanobanana(mcp__nanobanana__generate_image, model="pro")로
미리 생성해서 경로를 넘겨줄 것 — 이 스크립트 자체는 이미지를 생성하지 않는다
(CLAUDE.md 이미지 생성 규칙: 반드시 nanobanana pro 사용, 이건 Claude 세션에서만
호출 가능한 MCP 도구라 완전 무인 스케줄 실행에서는 이미지 없이 텍스트+표만 발행됨).
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
    parser.add_argument("--image", default=None, help="대표 이미지로 삽입할 로컬 파일 경로 (nanobanana로 미리 생성)")
    args = parser.parse_args()

    if not args.force and not args.dry_run and was_posted_this_week():
        logger.info("이번 주 이미 블로그 글이 발행됨. 건너뜀. (강제 실행: --force)")
        return

    post = generate_blog_post()
    logger.info(f"글 생성 완료 — 시리즈: {post['series']}")
    logger.info("제목 후보:\n" + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(post["title_candidates"])))
    logger.info(f"후킹유형={post['hook_type']} 비유분야={post['metaphor_domain']}")
    logger.info(f"\n{'='*50}\n{post['body']}\n{'='*50}")
    logger.info(f"태그: {post['tags']}")
    if len(post["image_specs"]) < 5:
        logger.warning(f"이미지 스펙 {len(post['image_specs'])}개 (완전 무인 실행이라 --image 없이는 텍스트+표만 발행됨)")

    url = post_to_naver_blog(post["title"], post["body"], post["tags"], image_path=args.image, dry_run=args.dry_run)

    if not args.dry_run:
        log_post(
            post["series"],
            post["title"],
            url,
            post["tags"],
            hook_type=post["hook_type"],
            metaphor_domain=post["metaphor_domain"],
            signature=post["signature"],
            elements_used=post["elements_used"],
        )
        logger.info(f"발행 및 로그 기록 완료: {url}")
    else:
        logger.info("dry-run 모드 — 로그 기록 생략. data/naver_dry_run.png 확인 후 --dry-run 없이 재실행하세요.")


if __name__ == "__main__":
    main()
