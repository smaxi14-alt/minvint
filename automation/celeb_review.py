"""셀럽 트렌드 블로그 — STAGE 7: 사후 검수 CLI + kill switch.

완전 자동 운영이므로 발행 전 사람 승인은 없다(개발 지시서 STAGE 5가 그 역할을
대신함). 대신 발행 "후"에 사람이 주기적으로 점검하고, 문제 시 즉시 삭제/수정한다
(개발 지시서 STAGE 7). killswitch는 특정 셀럽 관련 글을 한 번에 전부 내리는
비상 기능 — naver_blog_poster.delete_post()를 그대로 재사용한다(이미 실전
검증된 함수).

사용법:
  python celeb_review.py list
  python celeb_review.py show <id>
  python celeb_review.py mark <id> keep|edit|takedown
  python celeb_review.py killswitch "<셀럽명>"
  python celeb_review.py add-ig "<셀럽명>" <instagram_post_url>
"""
import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import celeb_db as db
from config import DATA_DIR, CELEB_NAVER_BLOG_ID, CELEB_NAVER_BLOG_PW
from naver_blog_poster import delete_post

CELEB_PROFILE_DIR = DATA_DIR / "naver_chrome_profile_celeb"


def cmd_list(args):
    posts = db.list_recent_published(limit=args.limit)
    if not posts:
        print("발행된 글이 없습니다.")
        return
    for p in posts:
        print(f"[{p['id']}] {p['published_at'][:16]} | {p['celeb_name']} | "
              f"검수상태={p['post_review_status']} | {p['title'][:40]}")
        print(f"      {p['url']}")


def cmd_show(args):
    p = db.get_published(args.id)
    if p is None:
        print(f"id={args.id} 글을 찾을 수 없습니다.")
        return
    print(f"제목: {p['title']}")
    print(f"셀럽: {p['celeb_name']}")
    print(f"URL: {p['url']}")
    print(f"발행일: {p['published_at']}")
    print(f"검수 상태: {p['post_review_status']}")
    print(f"\n팩트 출처 스냅샷:")
    for f in json.loads(p["fact_sources"] or "[]"):
        print(f"  - {f}")
    print(f"\n에셋 출처 스냅샷:")
    for a in json.loads(p["asset_refs"] or "[]"):
        print(f"  - {a}")


def cmd_mark(args):
    if args.status not in ("keep", "edit", "takedown"):
        print("status는 keep/edit/takedown 중 하나여야 합니다.")
        return
    db.mark_post_review(args.id, args.status)
    print(f"id={args.id} 검수 상태를 '{args.status}'로 변경했습니다.")
    if args.status == "takedown":
        p = db.get_published(args.id)
        if p and p["log_no"]:
            confirm = input(f"'{p['title']}' 글을 지금 바로 삭제할까요? (복구 불가) [y/N]: ")
            if confirm.strip().lower() == "y":
                ok = delete_post(p["log_no"], blog_id=CELEB_NAVER_BLOG_ID,
                                  blog_pw=CELEB_NAVER_BLOG_PW, profile_dir=CELEB_PROFILE_DIR)
                print("삭제 완료" if ok else "삭제 실패 — 로그 확인")


def cmd_killswitch(args):
    posts = db.get_published_by_celeb(args.celeb_name)
    if not posts:
        print(f"'{args.celeb_name}' 관련 발행 글이 없습니다.")
        return
    print(f"'{args.celeb_name}' 관련 글 {len(posts)}개:")
    for p in posts:
        print(f"  [{p['id']}] {p['title']} — {p['url']}")
    confirm = input(f"\n위 {len(posts)}개 글을 전부 삭제할까요? (복구 불가) [y/N]: ")
    if confirm.strip().lower() != "y":
        print("취소되었습니다.")
        return
    for p in posts:
        if not p["log_no"]:
            print(f"  [{p['id']}] log_no 없음 — 건너뜀")
            continue
        ok = delete_post(p["log_no"], blog_id=CELEB_NAVER_BLOG_ID,
                          blog_pw=CELEB_NAVER_BLOG_PW, profile_dir=CELEB_PROFILE_DIR)
        if ok:
            db.mark_post_review(p["id"], "takedown")
            print(f"  [{p['id']}] 삭제 완료")
        else:
            print(f"  [{p['id']}] 삭제 실패")


def cmd_add_ig(args):
    db.add_instagram_url(args.celeb_name, args.url)
    print(f"'{args.celeb_name}' 워치리스트에 추가됨: {args.url}")


def main():
    parser = argparse.ArgumentParser(description="셀럽 블로그 사후 검수 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="최근 발행 목록")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="발행 글 상세(팩트·에셋 출처)")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    p_mark = sub.add_parser("mark", help="검수 상태 지정")
    p_mark.add_argument("id", type=int)
    p_mark.add_argument("status", choices=["keep", "edit", "takedown"])
    p_mark.set_defaults(func=cmd_mark)

    p_kill = sub.add_parser("killswitch", help="특정 셀럽 관련 글 전부 삭제")
    p_kill.add_argument("celeb_name")
    p_kill.set_defaults(func=cmd_killswitch)

    p_ig = sub.add_parser("add-ig", help="Instagram 워치리스트에 URL 추가")
    p_ig.add_argument("celeb_name")
    p_ig.add_argument("url")
    p_ig.set_defaults(func=cmd_add_ig)

    args = parser.parse_args()
    db.init_db()
    args.func(args)


if __name__ == "__main__":
    main()
