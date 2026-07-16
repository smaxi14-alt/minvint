"""
이미지 포함 Threads 발행 스크립트
Buffer REST API v1 → 이미지 업로드 → GraphQL 발행
"""
import json
import logging
import sys
import time
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BUFFER_ACCESS_TOKEN, BUFFER_CHANNEL_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

API_BASE = "https://api.buffer.com"

GQL_HEADERS = {
    "Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Origin": "https://publish.buffer.com",
    "Referer": "https://publish.buffer.com/",
}


def upload_image(image_path: Path) -> str:
    """Buffer 미디어 업로드 → 공개 URL 반환"""
    logger.info(f"이미지 업로드 중: {image_path.name}")
    with open(image_path, "rb") as f:
        image_data = f.read()

    # 시도 1: access_token 폼 파라미터
    r = requests.post(
        f"{API_BASE}/1/media/upload.json",
        data={"access_token": BUFFER_ACCESS_TOKEN},
        files={"file": (image_path.name, image_data, "image/png")},
        timeout=60,
    )
    logger.info(f"업로드 응답 ({r.status_code}): {r.text[:300]}")

    # 시도 2: query parameter 방식
    if r.status_code in (400, 401, 403):
        r = requests.post(
            f"{API_BASE}/1/media/upload.json?access_token={BUFFER_ACCESS_TOKEN}",
            files={"media[photo]": (image_path.name, image_data, "image/png")},
            timeout=60,
        )
        logger.info(f"시도 2 응답 ({r.status_code}): {r.text[:300]}")

    r.raise_for_status()
    data = r.json()
    url = (
        data.get("url")
        or data.get("media_url")
        or (data.get("media") or {}).get("photo")
    )
    if not url:
        raise ValueError(f"URL 없음: {data}")
    logger.info(f"업로드 완료: {url}")
    return url


def _build_mutation(text: str, media_urls: list) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    if media_urls:
        photos = ", ".join(f'"{u}"' for u in media_urls)
        media_block = f'media: {{ photos: [{photos}] }}'
    else:
        media_block = ""
    return f"""
mutation {{
  createPost(input: {{
    channelId: "{BUFFER_CHANNEL_ID}"
    text: "{escaped}"
    {media_block}
    schedulingType: automatic
    mode: shareNow
  }}) {{
    ... on PostActionSuccess {{ post {{ id status dueAt }} }}
    ... on MutationError {{ message }}
    ... on InvalidInputError {{ message }}
    ... on LimitReachedError {{ message }}
    ... on NotFoundError {{ message }}
  }}
}}
"""


def post_to_threads(text: str, image_paths: list = None) -> str:
    """이미지 포함 Threads 발행"""
    media_urls = []
    if image_paths:
        for path in image_paths:
            url = upload_image(Path(path))
            media_urls.append(url)
            time.sleep(1)

    logger.info(f"발행 중... 이미지 {len(media_urls)}개")
    payload = json.dumps({"query": _build_mutation(text, media_urls)}).encode("utf-8")

    r = requests.post(API_BASE, data=payload, headers=GQL_HEADERS, timeout=30)
    logger.info(f"발행 응답 ({r.status_code}): {r.text[:300]}")
    r.raise_for_status()

    result = r.json()
    create = result.get("data", {}).get("createPost", {})
    post = create.get("post")
    if post:
        logger.info(f"발행 성공: id={post['id']} status={post['status']}")
        return post["id"]

    error = create.get("message") or str(result)
    raise ValueError(f"발행 실패: {error}")


if __name__ == "__main__":
    TEXT = """월급이 높을수록 노후 준비가 잘 돼 있을 것 같죠.
15년 상담하면서 보면, 꼭 그렇지 않습니다.

계산해봤습니다.

월급 300만원, 저축률 25% → 매달 75만원 모음
월급 700만원, 저축률 8% → 매달 56만원 모음

고소득자가 매달 덜 모읍니다.

패턴이 있었습니다.
소득이 오를 때 지출도 같이 올랐습니다.
자녀 학원비, 차 업그레이드, 외식 횟수.

저도 솔직히 10년 전보다 소득이 늘었는데
"여유 있다"는 느낌은 별로 없습니다.

노후는 소득이 결정하는 게 아니었습니다.
소득 대비 저축 비율이 결정합니다.

지금 저축률이 몇 %예요?"""

    BASE_DIR = Path(__file__).parent.parent
    IMAGES = [
        BASE_DIR / "콘텐츠" / "threads-저축률" / "image1-hook.png",
        BASE_DIR / "콘텐츠" / "threads-저축률" / "image2-compare.png",
    ]

    post_id = post_to_threads(TEXT, IMAGES)
    print(f"\n발행 완료: {post_id}")
