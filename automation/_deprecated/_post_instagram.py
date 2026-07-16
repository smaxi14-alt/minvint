"""Buffer 업로드 + Instagram 발행 (임시 실행 스크립트)"""
import json
import os
import secrets
import subprocess
import time
import urllib.request
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")

TOKEN  = os.getenv("BUFFER_ACCESS_TOKEN", "")
IG_CH  = os.getenv("BUFFER_INSTAGRAM_CHANNEL_ID", "")
API    = "https://api.buffer.com"

SLIDE_DIR  = REPO_ROOT / "콘텐츠" / "instagram" / "2026-05-19"
SLIDES     = [SLIDE_DIR / f"slide0{i}.png" for i in range(1, 6)]
GITHUB_RAW = "https://raw.githubusercontent.com/smaxi14-alt/minvint/main"
SLIDE_URLS = [
    f"{GITHUB_RAW}/%EC%BD%98%ED%85%90%EC%B8%A0/instagram/2026-05-19/slide0{i}.png"
    for i in range(1, 6)
]
CAPTION   = (
    "월 78만원, 40대 직장인 평균 보험료\n"
    "그 중 절반이 불필요한 중복보장입니다.\n\n"
    "13년 경력의 보험 증권 진단가가 숫자로 증명합니다.\n"
    "보장은 그대로, 비용만 줄이는 방법 — 지금 바로 확인하세요.\n\n"
    "#보험증권진단 #보험료절감 #40대보험"
)


def upload(path: Path) -> str:
    boundary = "----FB" + secrets.token_hex(8)
    with open(path, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{API}/v1/media/upload",
        data=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        res = json.loads(r.read())

    url = res.get("url") or res.get("picture")
    if not url:
        raise ValueError(f"URL 없음: {res}")
    print(f"  업로드: {path.name} → {url[:70]}")
    return url


def post(media_urls: list[str]) -> str:
    esc   = CAPTION.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    items = ", ".join(f'{{ url: "{u}", type: IMAGE }}' for u in media_urls)
    mutation = f"""
mutation {{
  createPost(input: {{
    channelId: "{IG_CH}"
    text: "{esc}"
    media: [{items}]
    mode: shareNow
  }}) {{
    ... on PostActionSuccess {{ post {{ id status dueAt }} }}
    ... on MutationError {{ message }}
    ... on InvalidInputError {{ message }}
    ... on LimitReachedError {{ message }}
  }}
}}
"""
    payload = json.dumps({"query": mutation}).encode()
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Origin": "https://publish.buffer.com",
        "Referer": "https://publish.buffer.com/",
    }
    req = urllib.request.Request(API, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())
    print(json.dumps(result, ensure_ascii=False, indent=2))

    cp   = result.get("data", {}).get("createPost", {})
    post_obj = cp.get("post")
    if post_obj:
        return post_obj["id"]
    raise ValueError(cp.get("message") or str(result))


def log_and_commit(post_id: str):
    log_file = BASE_DIR / "logs" / "instagram_log.json"
    data = {"posts": []}
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    data["posts"].append({
        "date": date.today().isoformat(),
        "caption": CAPTION[:80],
        "buffer_post_id": post_id,
        "slide_dir": str(SLIDE_DIR.relative_to(REPO_ROOT)),
    })
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    subprocess.run(
        ["git", "-C", str(REPO_ROOT), "add",
         str(SLIDE_DIR.relative_to(REPO_ROOT)),
         "automation/logs/instagram_log.json"],
        check=False
    )
    subprocess.run(
        ["git", "-C", str(REPO_ROOT), "commit", "-m",
         f"card: instagram 카드뉴스 발행 {date.today().isoformat()}"],
        check=False
    )
    print(f"git commit 완료")


if __name__ == "__main__":
    print("=== Instagram 카드뉴스 발행 시작 ===")
    print(f"채널 ID : {IG_CH}")
    print(f"슬라이드: {SLIDE_DIR}")
    for u in SLIDE_URLS:
        print(f"  URL: {u}")

    print(f"\n[1/2] Instagram 발행 ({len(SLIDE_URLS)}장 carousel)...")
    post_id = post(SLIDE_URLS)
    print(f"발행 성공: post_id={post_id}")

    print("\n[2/2] 로그 저장 + git commit...")
    log_and_commit(post_id)

    print("\n=== 완료 ===")
