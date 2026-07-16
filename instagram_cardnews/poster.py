"""Buffer GraphQL API로 Instagram 카드뉴스(캐러셀) 발행.

automation/buffer_poster.py(쓰레드, 텍스트 전용, 수개월째 매일 발행 중인
검증된 경로 — 코드는 참고만 하고 import는 하지 않는다, 2026-07-16 폴더 분리
결정)의 재시도(선형 백오프 30*(attempt+1)초, 기본 3회) 패턴을 그대로
재사용하되, 자격증명은 완전히 분리했다. Buffer 무료 플랜이 계정당 채널 1개만
허용해, 인스타그램은 쓰레드와 다른 Buffer 계정/조직에서 발급받은 BUFFER_IG_*
전용 변수를 쓴다.

2026-07-16 실측으로 발견 — Buffer GraphQL의 createPost `media` 필드는 로컬
파일 업로드가 아니라 **공개적으로 접근 가능한 URL**을 요구한다(REST
`/v1/media/upload` 엔드포인트로 직접 바이트를 올리면 "Unsupported
Content-Type" 400 오류). 이 저장소가 이미 공개(public)이므로, 이미지를
GitHub에 커밋+푸시해 raw.githubusercontent.com URL로 노출한 뒤 그 URL을
media 필드에 넘긴다 — 과거 콘텐츠/instagram/2026-05-19/slide0N.png가 이미
같은 방식으로 커밋된 전례가 있다(단, 그 스크립트도 실제 발행 성공 기록은
없었다). 첫 실제 카드뉴스 발행 전에 반드시 check_instagram_channel()로 채널
연결을 먼저 확인하고, 결과를 사람이 직접 확인해야 한다(session.py의 QA
게이트 참조).
"""
import json
import time
import logging
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

from config import (
    BUFFER_IG_ACCESS_TOKEN, BUFFER_IG_CHANNEL_ID, BUFFER_IG_ORGANIZATION_ID,
    GITHUB_RAW_BASE, REPO_ROOT,
)

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.buffer.com/graphql"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {BUFFER_IG_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Origin": "https://publish.buffer.com",
        "Referer": "https://publish.buffer.com/",
    }


def check_instagram_channel() -> dict | None:
    """BUFFER_IG_CHANNEL_ID가 실제로 연결된 채널인지 읽기 전용으로 확인한다.
    쿼리 형태는 실제 GraphQL introspection(__schema)으로 직접 확인해
    검증했다(2026-07-15) — `channels`는 `input: {organizationId}`가 필수이고,
    반환 타입 Channel의 필드는 `service`(평면 enum)·`displayName`·
    `isDisconnected`이다. 실제 발행 전 항상 먼저 호출할 것."""
    query = """
    query($input: ChannelsInput!) {
      channels(input: $input) {
        id
        name
        displayName
        service
        isDisconnected
      }
    }
    """
    payload = json.dumps({
        "query": query,
        "variables": {"input": {"organizationId": BUFFER_IG_ORGANIZATION_ID}},
    }).encode("utf-8")
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req) as res:
            result = json.loads(res.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"[cardnews] Buffer 채널 조회 실패: {e}")
        return None

    if result.get("errors"):
        logger.error(f"[cardnews] Buffer 채널 조회 GraphQL 오류: {result['errors']}")
        return None

    channels = result.get("data", {}).get("channels", [])
    match = next((c for c in channels if c.get("id") == BUFFER_IG_CHANNEL_ID), None)
    if not match:
        logger.error(
            "[cardnews] BUFFER_IG_CHANNEL_ID가 연결된 채널 목록에 없음 — "
            f".env 설정 확인 필요 (조회된 채널 {len(channels)}개: "
            f"{[(c.get('service'), c.get('name')) for c in channels]})"
        )
    return match


def _run_git(args: list[str]) -> tuple[int, str]:
    """Windows 콘솔 기본 코드페이지(cp949)가 한글 커밋 메시지를 못 읽어
    subprocess 리더 스레드가 죽는 문제 방지 — encoding 명시(2026-07-16 실측
    발견: UnicodeDecodeError로 stdout/stderr가 None이 되어 후속 .strip()이
    깨짐)."""
    r = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.returncode, (r.stderr or r.stdout or "").strip()


def publish_images_to_github(image_paths: list[Path], commit_message: str) -> list[str]:
    """이미지를 이 저장소(공개 GitHub 레포)에 커밋+푸시해 공개 URL을
    확보한다. Buffer의 media 필드가 로컬 업로드를 지원하지 않아 필요한
    경로 — 실제로 원격 저장소에 새 커밋을 만들고 푸시하는, 되돌리기 번거로운
    작업이므로 session.py가 사람이 있는 세션에서만 호출해야 한다."""
    rel_paths = [str(Path(p).relative_to(REPO_ROOT)) for p in image_paths]

    code, msg = _run_git(["add", *rel_paths])
    if code != 0:
        raise RuntimeError(f"git add 실패: {msg}")

    code, msg = _run_git(["commit", "-m", commit_message])
    # "커밋할 변경사항 없음"은 실패가 아니다 — 이전 시도(예: 이후 단계에서
    # 실패해 재시도하는 경우)에서 이미지가 이미 커밋+푸시됐을 수 있다.
    # git 버전/상황에 따라 문구가 "nothing to commit"뿐 아니라
    # "no changes added to commit"으로도 나타남을 실측 확인(2026-07-16).
    nothing_to_commit = "nothing to commit" in msg or "no changes added to commit" in msg
    if code != 0 and not nothing_to_commit:
        raise RuntimeError(f"git commit 실패: {msg}")

    code, msg = _run_git(["pull", "--rebase", "origin", "main"])
    if code != 0:
        raise RuntimeError(f"git pull --rebase 실패(원격과 충돌 가능성) — 수동 확인 필요: {msg}")

    code, msg = _run_git(["push", "origin", "main"])
    if code != 0:
        raise RuntimeError(f"git push 실패: {msg}")

    urls = []
    for rel in rel_paths:
        encoded = "/".join(urllib.parse.quote(part) for part in rel.split("/"))
        urls.append(f"{GITHUB_RAW_BASE}/{encoded}")
    logger.info(f"[cardnews] 이미지 {len(urls)}장 GitHub 공개 URL 확보 완료")
    return urls


def _build_carousel_mutation(caption: str, media_urls: list[str]) -> str:
    """2026-07-16 실측 GraphQL introspection으로 확정한 실제 스키마:
    CreatePostInput.assets: [AssetInput!]!, AssetInput.image: ImageAssetInput,
    ImageAssetInput.url: String!. 기존 `media: [{url,type}]` 필드는 스키마에
    아예 존재하지 않아 "Field media is not defined by type CreatePostInput"
    검증 오류로 항상 실패했다(과거 실험 스크립트 _post_instagram.py의 추측이
    틀렸던 것으로 확인됨).

    또한 인스타그램은 metadata.instagram.type이 필수다("Instagram posts
    require a type" 오류로 실측 확인) — PostType enum에 실제로 존재하는
    "carousel" 값을 사용(다른 값: post/reel/story/short 등, introspection으로
    확인). shouldShareToFeed도 NON_NULL이라 항상 명시해야 한다.
    isAiGenerated는 실제로 AI가 생성한 콘텐츠이므로 정직하게 true로 표기."""
    escaped = caption.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    asset_items = ", ".join(f'{{ image: {{ url: "{u}" }} }}' for u in media_urls)
    return f"""
mutation {{
  createPost(input: {{
    channelId: "{BUFFER_IG_CHANNEL_ID}"
    text: "{escaped}"
    assets: [{asset_items}]
    metadata: {{
      instagram: {{
        type: carousel
        shouldShareToFeed: true
        isAiGenerated: true
      }}
    }}
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


def post_carousel(caption: str, media_urls: list[str], retries: int = 3) -> str:
    """캐러셀 발행 자체(미디어는 이미 GitHub에 올라가 공개 URL이 확보된
    상태여야 함)."""
    payload = json.dumps({"query": _build_carousel_mutation(caption, media_urls)}).encode("utf-8")

    for attempt in range(retries):
        try:
            req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=_headers(), method="POST")
            with urllib.request.urlopen(req) as res:
                result = json.loads(res.read().decode("utf-8"))

            create_post = result.get("data", {}).get("createPost", {})
            post = create_post.get("post")
            if post:
                logger.info(f"[cardnews] Buffer IG 발행 성공: id={post['id']} status={post['status']}")
                return post["id"]

            error_msg = create_post.get("message") or str(result)
            raise ValueError(f"Buffer 응답 오류: {error_msg}")

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logger.error(f"[cardnews] 시도 {attempt + 1}/{retries} HTTP {e.code}: {body[:500]}")
        except Exception as e:
            logger.error(f"[cardnews] 시도 {attempt + 1}/{retries} 실패: {e}")

        if attempt < retries - 1:
            wait = 30 * (attempt + 1)
            logger.info(f"[cardnews] {wait}초 후 재시도...")
            time.sleep(wait)

    raise RuntimeError(f"Buffer IG 발행 {retries}회 모두 실패")


def publish_cardnews(caption: str, image_paths: list[Path], commit_message: str) -> str:
    """전체 오케스트레이션: 이미지 GitHub 공개 호스팅 → 캐러셀 발행."""
    media_urls = publish_images_to_github(image_paths, commit_message)
    return post_carousel(caption, media_urls)
