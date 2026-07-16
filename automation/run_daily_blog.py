"""1일 1글 네이버 블로그 완전 자동 발행 오케스트레이터.

매일 11:00 KST에 Windows 작업 스케줄러(setup_blog_scheduler.ps1, 작업명
NaverBlog-Daily)가 이 스크립트를 실행한다. 사람 개입 없이 아래를 전부 수행한다:
오늘의 키워드 선택(blog_keywords) → 본문 생성(blog_generator) → 안전 점검
(blog_safety_check) → 이미지 생성(blog_image_direct — CLAUDE.md에 명시된 유일한
직접 API 예외) → 네이버 발행(naver_blog_poster) → 로그 기록(blog_tracker).

실패해도 예외를 상위로 전파하지 않는다 — 로그만 남기고 조용히 종료한다.
스케줄러가 매일 재시도하므로 하루 실패했다고 프로세스를 비정상 종료시켜
알림 폭탄을 만들 필요는 없다. 각 실패 지점은 logs/blog_daily.log에
[STATUS=...] 태그로 남아 나중에 grep으로 추적 가능하다
(_context/blog-daily-automation-strategy.md §8 재시도 정책 참조).

수동 테스트: python run_daily_blog.py --dry-run
(발행 직전까지 전부 실행하되 네이버에는 스크린샷만 남기고 실제 발행하지 않음)
"""
import argparse
import json
import logging
import sys
from datetime import date

# Windows 콘솔 기본 코드페이지(cp949)가 em-dash 등 일부 문자를 못 그려 로그가
# 매일 UnicodeEncodeError로 오염되는 것을 방지 (diversity_monitor.py와 동일 패턴).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from config import DATA_DIR
from blog_tracker import was_posted_today, log_post
from blog_keywords import pick_today_keyword
from blog_generator import generate_blog_post, BLOG_SERIES
from blog_safety_check import run_all_checks, check_title
from blog_image_direct import generate_images_for_specs
from naver_blog_poster import post_to_naver_blog

IMAGES_DIR = DATA_DIR / "blog_images"
NEEDS_REVIEW_DIR = DATA_DIR / "needs_review"

# 2026-07-15 신설 — "여러 문제가 생기면 멈추지 말고 고쳐서 재발행하라"는 요구
# 대응. content_generator.py(Threads 파이프라인)에 이미 있던 검증된 패턴
# (재생성 3회 + 실패 사유 프롬프트 피드백 + 소진 시 마지막 시도 강행 발행)을
# 그대로 이식한다.
MAX_GENERATION_ATTEMPTS = 3
MAX_PUBLISH_ATTEMPTS = 2


def _series_dict_for(series_name: str) -> dict | None:
    for s in BLOG_SERIES:
        if s["name"] == series_name:
            return s
    return None


def _save_needs_review(post: dict, reason: str, detail):
    """자동 처리를 중단해야 하는 상황을 사람이 나중에 확인할 수 있게 저장한다.
    자동 폐기도, 문제를 무시한 강행 발행도 하지 않는다 — 보류만 한다."""
    NEEDS_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = NEEDS_REVIEW_DIR / f"{date.today().isoformat()}.json"
    payload = {**post, "review_reason": reason, "review_detail": detail}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.error(f"발행 중단 — 수동 검토 필요: {path} ({reason})")


def _save_audit_record(post: dict, reason: str, detail):
    """발행은 강행하되(needs_review처럼 막지 않음) 나중에 주간 감사에서 걸러볼
    수 있게 기록만 남긴다(2026-07-15 신설). MAX_GENERATION_ATTEMPTS를 다 써도
    위반이 남으면 마지막 시도를 그대로 발행하기로 사용자가 확정한 정책 —
    content_generator.py의 기존 Threads 파이프라인과 동일하게, 발행을 막는 게
    아니라 사후 추적만 남긴다."""
    NEEDS_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = NEEDS_REVIEW_DIR / f"published_despite_violations_{date.today().isoformat()}.json"
    payload = {**post, "review_reason": reason, "review_detail": detail}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.warning(f"[STATUS=PUBLISHED_WITH_VIOLATIONS] 강행 발행 기록: {path} ({reason})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="생성·이미지·QC까지 전부 실행하되 네이버 발행은 스크린샷만 남기고 실제로 하지 않음",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="1일 1글 중복 방지 가드를 무시하고 발행 (스케줄러 장애로 놓친 날짜를 메이크업 발행할 때만 사용)",
    )
    args = parser.parse_args()

    if was_posted_today() and not args.force:
        logger.info("[STATUS=SKIPPED] 오늘 이미 발행됨 — 종료 (1일 1글 원칙, 중복 실행 방지)")
        return
    if args.force:
        logger.warning("[STATUS=FORCE] --force로 1일 1글 가드 우회 — 메이크업 발행")

    keyword_spec = pick_today_keyword()
    series = _series_dict_for(keyword_spec.get("suggested_series"))
    logger.info(
        f"오늘의 타깃 — tier={keyword_spec['tier']} keyword={keyword_spec['keyword']} "
        f"combo_key={keyword_spec['combo_key']} series={keyword_spec['suggested_series']}"
    )

    post = None
    violations: list[str] = []
    avoid_violations = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            post = generate_blog_post(series=series, keyword_spec=keyword_spec, avoid_violations=avoid_violations)
        except Exception:
            logger.error(f"[STATUS=GEN_FAILED] 본문 생성 실패 (시도 {attempt}/{MAX_GENERATION_ATTEMPTS})", exc_info=True)
            if attempt == MAX_GENERATION_ATTEMPTS:
                return
            continue

        violations = run_all_checks(post["body"], post["tags"], post["title"])
        if not violations:
            break

        # 1차 저비용 복구 — 위반이 전부 제목 문제뿐이면 재생성 없이
        # title_candidates 중 통과하는 후보로 즉시 교체(API 호출 없음).
        if all(v.startswith("제목 금지 표현 검출") for v in violations):
            for cand in post.get("title_candidates", [])[1:]:
                if not check_title(cand):
                    logger.info(f"제목 교체로 위반 해소: '{post['title']}' → '{cand}'")
                    post["title"] = cand
                    violations = run_all_checks(post["body"], post["tags"], post["title"])
                    break
            if not violations:
                break

        if attempt == MAX_GENERATION_ATTEMPTS:
            break  # 아래에서 강행 발행 여부를 판단
        logger.warning(
            f"[STATUS=QC_RETRY] 안전 점검 위반 {len(violations)}건(시도 {attempt}/{MAX_GENERATION_ATTEMPTS}): "
            f"{violations} — 사유를 반영해 재생성"
        )
        avoid_violations = violations

    if post is None:
        return  # 생성 자체가 MAX_GENERATION_ATTEMPTS 내내 실패

    if violations:
        # 재생성을 다 써도 위반이 남으면, 막지 않고 마지막 시도를 그대로 발행한다
        # (Threads 파이프라인 content_generator.py와 동일한 정책 — 사용자 확정).
        # 안전검사 자체를 우회하는 게 아니라, 3회 정직하게 재확인한 뒤의 최종
        # 선택이다. 발행 후 _save_audit_record()로 추적 기록을 남긴다.
        logger.warning(
            f"[STATUS=PUBLISHED_WITH_VIOLATIONS] {MAX_GENERATION_ATTEMPTS}회 재생성 후에도 위반 잔존: "
            f"{violations} — 마지막 시도를 그대로 발행 강행"
        )

    if len(post["image_specs"]) < 5:
        logger.warning(f"이미지 스펙 {len(post['image_specs'])}개뿐 (요청 5개 이상) — 있는 만큼 진행")

    # 이미지 생성 실패/저품질 사후 진단용 — 실제 사용된 프롬프트를 항상 남긴다.
    # (이미지 자체의 텍스트 오류는 blog_safety_check가 못 잡으므로, 발생 시
    # 이 파일로 어떤 프롬프트가 문제를 일으켰는지 역추적한다.)
    DATA_DIR.joinpath("last_run_post.json").write_text(
        json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    image_map = generate_images_for_specs(post["image_specs"], IMAGES_DIR)
    if len(image_map) < len(post["image_specs"]):
        missing_specs = [s for s in post["image_specs"] if s["index"] not in image_map]
        logger.info(f"이미지 {len(missing_specs)}개 부족 — 누락분만 1회 재시도")
        image_map.update(generate_images_for_specs(missing_specs, IMAGES_DIR))

    if len(image_map) < 3:
        _save_needs_review(
            post, "image_generation_insufficient",
            f"{len(image_map)}/{len(post['image_specs'])}개만 확보",
        )
        logger.error(
            f"[STATUS=IMAGE_INCOMPLETE] 이미지 {len(image_map)}/{len(post['image_specs'])}개만 확보 — 발행 중단"
        )
        return
    if len(image_map) < len(post["image_specs"]):
        logger.warning(
            f"이미지 {len(image_map)}/{len(post['image_specs'])}개만 확보 — 나머지는 마커만 건너뛰고 진행"
        )

    url = None
    for publish_attempt in range(1, MAX_PUBLISH_ATTEMPTS + 1):
        try:
            url = post_to_naver_blog(
                post["title"], post["body"], post["tags"],
                image_map=image_map, dry_run=args.dry_run,
            )
            break
        except Exception:
            logger.error(
                f"[STATUS=PUBLISH_RETRY] 네이버 발행 실패 (시도 {publish_attempt}/{MAX_PUBLISH_ATTEMPTS}) — "
                f"새 브라우저 세션으로 재시도", exc_info=True,
            )
            if publish_attempt == MAX_PUBLISH_ATTEMPTS:
                _save_needs_review(post, "naver_publish_failed", "post_to_naver_blog 예외 — logs 확인 (재시도 소진)")
                logger.error("[STATUS=PUBLISH_FAILED] 네이버 발행 실패 — 재시도 소진")
                return

    if args.dry_run:
        logger.info(f"[STATUS=DRY_RUN] 발행하지 않음 — data/naver_dry_run.png 확인")
        return

    log_post(
        post["series"], post["title"], url, post["tags"],
        hook_type=post.get("hook_type", ""),
        metaphor_domain=post.get("metaphor_domain", ""),
        signature=post.get("signature", ""),
        elements_used=post.get("elements_used", []),
        keyword=post.get("keyword", ""),
        tier=post.get("tier", ""),
        combo_key=post.get("combo_key", ""),
        cta_style=post.get("cta_style", ""),
    )
    logger.info(f"[STATUS=SUCCESS] 발행 완료: {url}")

    if violations:
        _save_audit_record(post, "published_despite_violations", violations)


if __name__ == "__main__":
    main()
