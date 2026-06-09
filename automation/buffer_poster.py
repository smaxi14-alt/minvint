"""
Buffer GraphQL API로 Threads 발행.
어제 buffer_post.py에서 검증된 방식 그대로 사용.
"""
import json
import time
import logging
import urllib.request
import urllib.error
from config import BUFFER_ACCESS_TOKEN, BUFFER_CHANNEL_ID

logger = logging.getLogger(__name__)

API_URL = "https://api.buffer.com"

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Origin": "https://publish.buffer.com",
        "Referer": "https://publish.buffer.com/",
    }


def _build_mutation(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f"""
mutation {{
  createPost(input: {{
    channelId: "{BUFFER_CHANNEL_ID}"
    text: "{escaped}"
    schedulingType: automatic
    mode: shareNow
  }}) {{
    ... on PostActionSuccess {{
      post {{ id status dueAt }}
    }}
    ... on MutationError {{ message }}
    ... on InvalidInputError {{ message }}
    ... on LimitReachedError {{ message }}
    ... on NotFoundError {{ message }}
    ... on RestProxyError {{ message }}
  }}
}}
"""


def post_to_buffer(text: str, retries: int = 3) -> str:
    payload = json.dumps({"query": _build_mutation(text)}).encode("utf-8")

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_URL, data=payload, headers=_headers(), method="POST"
            )
            with urllib.request.urlopen(req) as res:
                result = json.loads(res.read().decode("utf-8"))

            create_post = result.get("data", {}).get("createPost", {})
            post = create_post.get("post")
            if post:
                logger.info(f"Buffer 발행 성공: id={post['id']} status={post['status']}")
                return post["id"]

            # GraphQL 에러 응답 처리
            error_msg = create_post.get("message") or str(result)
            raise ValueError(f"Buffer 응답 오류: {error_msg}")

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            logger.error(f"시도 {attempt + 1}/{retries} HTTP {e.code}: {body[:300]}")
        except Exception as e:
            logger.error(f"시도 {attempt + 1}/{retries} 실패: {e}")

        if attempt < retries - 1:
            wait = 30 * (attempt + 1)
            logger.info(f"{wait}초 후 재시도...")
            time.sleep(wait)

    raise RuntimeError(f"Buffer 발행 {retries}회 모두 실패")
