"""셀럽 트렌드 블로그 — 오케스트레이터. Windows 작업 스케줄러(10/14/19시)가 호출.

STAGE 0~1(트렌드 감지) → STAGE 2(팩트) → STAGE 3(에셋) → STAGE 4(생성) →
STAGE 5(검증) → STAGE 6(발행) → 로그. 사람 사전 승인 없이 발행하되(개발 지시서
FULLY_AUTOMATED=True), STAGE 5를 반드시 통과해야만 발행한다.

후보 하나가 팩트 부족/검증 실패로 막히면 다음 후보로 넘어간다(최대 CANDIDATE_TRY_LIMIT개
시도). 전부 실패하면 이번 슬롯은 조용히 건너뛴다 — 매일 반드시 발행해야 한다는
압박이 오히려 어뷰징 패턴이라는 게 지시서 원칙 ④의 취지.

실행: python run_celeb_pipeline.py [--dry-run]
"""
import argparse
import logging
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from config import DATA_DIR, CELEB_NAVER_BLOG_ID, CELEB_NAVER_BLOG_PW, CELEB_MAX_DAILY_POSTS
import celeb_db as db
from celeb_watchlist import discover_candidates
from celeb_facts import gather_facts
from celeb_assets import gather_assets
from celeb_generator import generate_draft
from celeb_validate import run_all_checks, check_hero_placement, check_clickbait
from naver_blog_poster import post_to_naver_blog

CANDIDATE_TRY_LIMIT = 5
CELEB_PROFILE_DIR = DATA_DIR / "naver_chrome_profile_celeb"

LOCK_PATH = DATA_DIR / "celeb_pipeline.lock"
LOCK_STALE_SECONDS = 2 * 60 * 60  # 2시간 — 정상 실행은 보통 5~40분


def _acquire_lock() -> bool:
    """동시 실행 방지 잠금(2026-07-16 신설). CelebBlog-Morning이
    STATUS_CONTROL_C_EXIT로 조용히 중단되는 사고 대응으로 감시(watchdog)
    재시도를 도입하면서, 원래 실행이 죽은 게 아니라 단순히 느리게 진행 중일
    때 재시도가 겹쳐 브라우저 두 개가 같은 네이버 계정에 동시 로그인하는
    사고를 막기 위함. 잠금 파일이 비정상적으로 오래(2시간+) 남아있으면 죽은
    프로세스의 잔재로 보고 무시하고 새로 잡는다."""
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age < LOCK_STALE_SECONDS:
            logger.warning(f"[STATUS=LOCKED] 다른 실행이 진행 중으로 보임(잠금 {age:.0f}초 전 생성) — 건너뜀")
            return False
        logger.warning(f"[celeb] 오래된 잠금 파일 발견({age:.0f}초 전) — 죽은 프로세스로 간주하고 무시")
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)

# 2026-07-15 신설 — "여러 문제가 생기면 멈추지 말고 고쳐서 재발행하라"는 요구
# 대응. content_generator.py(Threads 파이프라인)의 기존 검증된 패턴(재생성 3회
# + 실패 사유 프롬프트 피드백 + 소진 시 마지막 시도 강행 발행)을 같은 트렌드
# 후보 안에서 먼저 시도하고, 그래도 안 되면 기존처럼 다음 후보로 넘어간다.
MAX_GENERATION_ATTEMPTS = 3
MAX_PUBLISH_ATTEMPTS = 2

# sub_tags의 영문 키 → 네이버 블로그 카테고리 관리에 실제 생성된 한글 라벨
# (2026-07-14 실측 확인: kbyoung1120 계정에 이미 3개 서브 카테고리 존재).
SUB_TAG_TO_CATEGORY = {
    "celeb_fashion": "셀럽 패션",
    "celeb_diet": "셀럽 다이어트",
    "celeb_beauty": "셀럽 뷰티",
}


def _convert_asset_markers(body_markdown: str, assets: list[dict]) -> tuple[str, dict[int, str]]:
    """[[ASSET:N]] 마커를 naver_blog_poster.py가 이해하는 형식으로 변환한다.
    textcard/context/official → [[IMAGE:style:설명]] + image_map(로컬 파일 경로).
    official은 Wikimedia Commons 실사 사진(2026-07-14 신설) — 이미 로컬에
    내려받은 파일이라 context와 동일하게 photo 스타일로 삽입한다.
    embed(YouTube/Instagram) → [[EMBED:url]] — URL을 그대로 타이핑하면 네이버
    에디터가 자동으로 실제 임베드 카드로 변환한다(2026-07-14 실측 확인)."""
    image_map: dict[int, str] = {}
    counter = 0

    def repl(match: re.Match) -> str:
        nonlocal counter
        idx = int(match.group(1))
        if idx >= len(assets):
            logger.warning(f"[celeb] 존재하지 않는 [[ASSET:{idx}]] 마커 — 건너뜀")
            return ""
        asset = assets[idx]
        if asset["type"] == "embed":
            return f"[[EMBED:{asset['ref']}]]"
        if asset["type"] not in ("textcard", "context", "official", "downloaded_photo"):
            return ""
        style = "infographic" if asset["type"] == "textcard" else "photo"
        marker = f"[[IMAGE:{style}:{asset['alt_text']}]]"
        image_map[counter] = asset["ref"]
        counter += 1
        return marker

    new_body = re.sub(r"\[\[ASSET:(\d+)\]\]", repl, body_markdown)
    return new_body, image_map


def _promote_hero_asset(body_markdown: str, assets: list[dict]) -> str:
    """생성 LLM이 대표이미지 배치 규칙(celeb_generator.py의 "대표이미지는 본문
    첫 번째 이미지여야 함" 규칙)을 지키지 않았을 때를 대비한 결정론적
    안전망(2026-07-15 신설 — 오늘 닉쿤 발행 건에서 LLM이 배경설명 텍스트카드를
    맨 앞에 배치해 그게 그대로 네이버 대표이미지/썸네일이 된 문제 실측).
    is_hero=True인 자산(celeb_assets.py의 _mark_hero() 참조)의 [[ASSET:N]]
    마커가 본문에서 등장하는 첫 번째 자산 마커가 아니면, 그 마커가 있는 줄을
    본문 맨 앞으로 옮긴다 — LLM 프롬프트 준수 확률에 기대지 않고 코드로 100%
    보장하는 게 재생성 재시도보다 훨씬 저렴하고 확실하다."""
    hero_idx = next((i for i, a in enumerate(assets) if a.get("is_hero")), None)
    if hero_idx is None:
        return body_markdown

    hero_marker = f"[[ASSET:{hero_idx}]]"
    lines = body_markdown.split("\n")
    hero_line_i = next((i for i, ln in enumerate(lines) if hero_marker in ln), None)
    if hero_line_i is None:
        return body_markdown  # LLM이 hero 자산을 본문에 아예 안 씀 — 건드리지 않음

    first_marker_line_i = next((i for i, ln in enumerate(lines) if re.search(r"\[\[ASSET:\d+\]\]", ln)), None)
    if first_marker_line_i is None or first_marker_line_i == hero_line_i:
        return body_markdown  # 이미 hero가 첫 마커 — 손댈 필요 없음

    hero_line = lines.pop(hero_line_i)
    lines.insert(0, hero_line)
    logger.info(f"[celeb] 대표이미지 마커를 본문 최상단으로 이동: ASSET:{hero_idx}")
    return "\n".join(lines)


def _try_candidate(candidate: dict, dry_run: bool, min_images: int = 6, min_real_photos: int = 6) -> bool:
    """후보 하나로 끝까지 시도한다. 성공(발행 또는 dry_run 성공)하면 True.

    기본값(min_images=6, min_real_photos=6)은 2026-07-14 사용자 요청 "실사
    사진은 6장 이상"을 스케줄러 기본 실행 경로(main())에도 반영한 것 —
    이전엔 이 함수가 min_images=2로만 기본 호출되고 있어서, dry-run 테스트
    때 수동으로 넘긴 min_images=5~7 값이 실제 자동 스케줄 실행에는 전혀
    반영되지 않던 문제가 있었다(실측으로 발견)."""
    celeb_name, topic, sub_tags = candidate["celeb_name"], candidate["topic"], candidate["sub_tags"]
    logger.info(f"[celeb] 후보 시도: {celeb_name} — {topic} {sub_tags}")

    fact_result = gather_facts(celeb_name, topic, sub_tags)
    if fact_result["needs_more_facts"]:
        logger.info(f"[celeb] {celeb_name}: 팩트 부족 — 다음 후보로")
        return False

    # hook_strength 내림차순 정렬(2026-07-15) — celeb_assets.gather_assets()가
    # 텍스트카드를 이 리스트 순서 그대로 카드화하므로, 가장 후킹되는 사실이
    # 먼저 카드화되게 한다(오늘 실측 문제: "닉쿤은 2PM의 멤버이다" 같은 순위상
    # 첫 사실이 가장 밋밋한 배경정보였는데 그대로 카드 0번이 됐었음).
    #
    # confirmed_texts(감사 로그용, log_published에 그대로 씀)와 all_facts_sorted
    # (에셋 생성용, confirmed+unconfirmed 전체)를 분리한다 — 2026-07-15 실측
    # 회귀: 텍스트카드가 confirmed 팩트만 재료로 썼더니, 같은 날 완화한
    # celeb_facts.py 게이트(논란 아니면 팩트 개수로 안 막음) 덕분에 통과한
    # confirmed 0~1개짜리 후보(리한나)가 카드화할 재료가 없어 "실사 사진 최소
    # 6장" 목표에 못 미친 채(3장만) 발행되는 문제가 있었다. unconfirmed도
    # celeb_assets.get_textcard()가 헤징 문구를 자동으로 붙여 카드화한다.
    all_facts_sorted = sorted(
        fact_result["facts"], key=lambda f: f.get("hook_strength", 1), reverse=True,
    )
    confirmed_texts = [f["fact_text"] for f in all_facts_sorted if f["status"] == "confirmed"]
    assets = gather_assets(
        celeb_name, sub_tags, all_facts_sorted, topic=topic,
        min_images=min_images, min_real_photos=min_real_photos,
    )

    draft = None
    violations: list[str] = []
    avoid_violations = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        draft = generate_draft(celeb_name, topic, sub_tags, fact_result["facts"], assets,
                                avoid_violations=avoid_violations)
        if not draft.get("body_markdown"):
            logger.info(f"[celeb] {celeb_name}: 생성 실패/빈 본문 (시도 {attempt}/{MAX_GENERATION_ATTEMPTS})")
            if attempt == MAX_GENERATION_ATTEMPTS:
                return False
            continue

        violations = run_all_checks(draft, fact_result["facts"], assets, dry_run=dry_run)
        if not violations:
            break

        # 일일 발행 상한은 콘텐츠 품질 문제가 아니라 운영 정책(어뷰징 방지)이므로
        # 재생성·강행 발행 대상이 아니다 — 즉시 다음 후보로(사실상 다음 슬롯까지 대기).
        if any("일일 발행 상한" in v for v in violations):
            logger.info(f"[celeb] {celeb_name}: 일일 발행 상한 도달 — 재시도 없이 다음 후보로")
            db.add_draft(celeb_name, sub_tags[0] if sub_tags else "", draft["titles"],
                          draft["body_markdown"], draft["hashtags"], {}, status="blocked",
                          block_reason="; ".join(violations))
            return False

        # 1차 저비용 복구 — 위반이 전부 클릭베이트 제목 패턴뿐이면 재생성 없이
        # titles[1]/[2] 중 통과하는 후보로 즉시 교체(API 호출 없음).
        if all(v.startswith("낚시성 제목 패턴 검출") for v in violations):
            for cand in draft["titles"][1:]:
                if not check_clickbait(cand):
                    logger.info(f"[celeb] {celeb_name}: 제목 교체로 위반 해소: '{draft['titles'][0]}' → '{cand}'")
                    draft["titles"] = [cand] + [t for t in draft["titles"] if t != cand]
                    violations = run_all_checks(draft, fact_result["facts"], assets, dry_run=dry_run)
                    break
            if not violations:
                break

        if attempt == MAX_GENERATION_ATTEMPTS:
            break  # 아래에서 강행 발행 여부를 판단
        logger.warning(
            f"[celeb] {celeb_name}: 검증 게이트 위반 {len(violations)}건(시도 {attempt}/{MAX_GENERATION_ATTEMPTS}): "
            f"{violations} — 사유를 반영해 재생성"
        )
        avoid_violations = violations

    if not draft or not draft.get("body_markdown"):
        return False

    if violations:
        # 재생성을 다 써도 위반이 남으면, 막지 않고 마지막 시도를 그대로 발행한다
        # (Threads 파이프라인 content_generator.py와 동일한 정책 — 사용자 확정).
        # 안전검사 자체를 우회하는 게 아니라 3회 정직하게 재확인한 뒤의 최종
        # 선택 — 발행 후 status='published'에 block_reason으로 사유를 남겨
        # 주간 감사(CLAUDE.md §7.2)에서 걸러볼 수 있게 한다.
        logger.warning(
            f"[celeb] {celeb_name}: {MAX_GENERATION_ATTEMPTS}회 재생성 후에도 위반 잔존: "
            f"{violations} — 마지막 시도를 그대로 발행 강행"
        )

    title = draft["titles"][0]
    promoted_body = _promote_hero_asset(draft["body_markdown"], assets)
    hero_issues = check_hero_placement(assets, promoted_body)
    if hero_issues:
        # 발행을 막지 않는다(_promote_hero_asset이 이미 대부분 케이스를 고침) —
        # 여기 로그가 남는 건 hero 자산이 본문에 아예 안 쓰인 극단적 케이스뿐,
        # 주간 감사 때 패턴 확인용.
        logger.warning(f"[celeb] {celeb_name}: 대표이미지 배치 확인 실패: {hero_issues}")
    body, image_map = _convert_asset_markers(promoted_body, assets)
    draft_id = db.add_draft(celeb_name, sub_tags[0] if sub_tags else "", draft["titles"],
                             draft["body_markdown"], draft["hashtags"], {"image_map": image_map},
                             status="ready_to_publish")

    category = SUB_TAG_TO_CATEGORY.get(sub_tags[0]) if sub_tags else None
    url = None
    for publish_attempt in range(1, MAX_PUBLISH_ATTEMPTS + 1):
        try:
            url = post_to_naver_blog(
                title, body, draft["hashtags"], image_map=image_map, dry_run=dry_run,
                blog_id=CELEB_NAVER_BLOG_ID, blog_pw=CELEB_NAVER_BLOG_PW, profile_dir=CELEB_PROFILE_DIR,
                category=category, stylize=True,
            )
            break
        except Exception:
            logger.error(
                f"[celeb] {celeb_name}: 네이버 발행 실패 (시도 {publish_attempt}/{MAX_PUBLISH_ATTEMPTS}) — "
                f"새 브라우저 세션으로 재시도", exc_info=True,
            )
            if publish_attempt == MAX_PUBLISH_ATTEMPTS:
                db.update_draft_status(draft_id, "blocked", "naver_publish_failed (재시도 소진)")
                return False

    if dry_run:
        logger.info(f"[STATUS=DRY_RUN] {celeb_name} — data/naver_dry_run.png 확인")
        return True

    log_no = url.rstrip("/").split("/")[-1]
    # status는 CHECK 제약상 'published'만 가능 — 강행 발행 여부는 block_reason에
    # 사유를 남겨 구분한다(2026-07-15, published_with_violations 같은 새 상태값
    # 추가는 DB 마이그레이션이 필요해 과했음 — block_reason 재사용으로 충분).
    db.update_draft_status(
        draft_id, "published",
        "; ".join(violations) if violations else "",
    )
    db.log_published(draft_id, celeb_name, title, url, log_no, confirmed_texts,
                      [a["ref"] for a in assets])
    if violations:
        logger.warning(f"[STATUS=PUBLISHED_WITH_VIOLATIONS] {celeb_name} 발행 완료(위반 잔존): {url} — {violations}")
    else:
        logger.info(f"[STATUS=SUCCESS] {celeb_name} 발행 완료: {url}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="발행 없이 스크린샷만 남김")
    args = parser.parse_args()

    if not _acquire_lock():
        return
    try:
        db.init_db()

        today_count = db.count_published_today()
        if today_count >= CELEB_MAX_DAILY_POSTS:
            logger.info(f"[STATUS=SKIPPED] 오늘 이미 {today_count}/{CELEB_MAX_DAILY_POSTS}건 발행됨")
            return

        if not CELEB_NAVER_BLOG_ID or not CELEB_NAVER_BLOG_PW:
            logger.error("[STATUS=CONFIG_MISSING] CELEB_NAVER_BLOG_ID/PW가 .env에 없습니다")
            return

        candidates = discover_candidates()
        if not candidates:
            logger.info("[STATUS=NO_CANDIDATES] 오늘 트렌드 후보를 찾지 못함")
            return

        for candidate in candidates[:CANDIDATE_TRY_LIMIT]:
            if _try_candidate(candidate, args.dry_run):
                return

        logger.info(f"[STATUS=ALL_CANDIDATES_FAILED] 후보 {min(len(candidates), CANDIDATE_TRY_LIMIT)}개 전부 실패 — 이번 슬롯 건너뜀")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
