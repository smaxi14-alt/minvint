"""셀럽 트렌드 블로그 — STAGE 3: 이미지 조달.

원래 원칙 ②(셀럽 사진 무단 다운로드·재업로드 금지)는 임베드 전용 경로
(YouTube/Instagram 공식 임베드, Wikimedia Commons 라이선스 사진)만 허용했다.
2026-07-14, 사용자가 임베드만으로는 원하는 실사 커버리지가 부족하다는 반복된
피드백 끝에 이 원칙을 명시적으로 해제하고 직접 다운로드를 요청해, 5번 경로
(get_downloaded_photos)를 신설했다. license_note에 항상 출처 도메인을 남겨
최소한의 추적 가능성만 유지한다.

1. YouTube 임베드 — search.list 멀티쿼리 폴백(2026-07-14 매칭률 개선)로 관련
   영상 자동 검색, URL을 본문에 타이핑하면 네이버가 실제 플레이어로 변환.
2. Instagram 임베드 — Serper.dev(구글 검색 프록시)로 site:instagram.com/p/
   검색해 게시물 URL 자동 발견, 없으면 instagram_watchlist 수동 큐레이션.
3. Wikimedia Commons 실사 사진 — 라이선스(CC 등) 명시된 경우만, 위키피디아
   등재 인물 위주라 커버리지 제한적.
4. Pexels 맥락(무드) 이미지 — 인물 없는 상황·소품 사진.
5. **다운로드 실사 사진**(get_downloaded_photos, 신설) — 네이버 이미지 검색
   API로 "{셀럽명} instagram" 등을 검색해 나온 이미지 파일을 직접 다운로드.
   워터마크·로고가 찍힌 이미지(스톡사진 재판매 워터마크, 팬사이트 로고 등)는
   Claude Vision으로 걸러낸다.
   2026-07-15 확장 — "네이버 이미지 검색만으로는 원하는 사진이 잘 안 나온다"는
   사용자 피드백 대응: SERPER_API_KEY가 설정돼 있으면 같은 후보 풀에 Serper
   구글 이미지 검색 + site:instagram.com 검색(인스타 실사 사진) 결과도 함께
   섞는다. 두 소스 모두 같은 Vision 품질/정체성 필터를 통과해야만 채택된다 —
   소스가 늘어도 원칙 ②(무단 사용 방지) 취지의 검증 절차는 동일하게 적용.
6. 카드형 텍스트 이미지 — blog_image_direct.py의 generate_image() 재사용.
"""
import base64
import logging
import re
import time
import urllib.request
import urllib.parse
import urllib.error
import json
from pathlib import Path

from anthropic import Anthropic
from config import (
    PEXELS_API_KEY, DATA_DIR, YOUTUBE_API_KEY, SERPER_API_KEY,
    ANTHROPIC_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET,
)
from blog_image_direct import generate_image
import celeb_db as db

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
IG_OEMBED_URL = "https://graph.facebook.com/v25.0/instagram_oembed"
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
NAVER_IMAGE_SEARCH_URL = "https://openapi.naver.com/v1/search/image"
SERPER_IMAGE_SEARCH_URL = "https://google.serper.dev/images"
SERPER_SEARCH_URL = "https://google.serper.dev/search"
_IG_POST_URL_RE = re.compile(r"https://www\.instagram\.com/(?:p|reel)/[A-Za-z0-9_-]+/?")

_PICK_VIDEO_TOOL = {
    "name": "pick_relevant_video",
    "description": "검색 결과 중 셀럽 본인이 실제로 등장하는 신뢰할 수 있는 영상을 고른다",
    "input_schema": {
        "type": "object",
        "properties": {
            "has_good_match": {"type": "boolean"},
            "chosen_index": {"type": "integer", "description": "has_good_match가 true일 때만 유효, 0-based 인덱스"},
            "reason": {"type": "string"},
        },
        "required": ["has_good_match", "chosen_index", "reason"],
    },
}


def _pick_best_youtube_result(celeb_name: str, topic: str, items: list[dict]) -> dict | None:
    """검색 결과 중 실제로 이 셀럽이 등장하는 신뢰할 수 있는 영상을 Claude로 선별한다.
    자동 검색이 동명이인·팬 편집물·낚시성 제목 같은 원치 않는 영상을 가져올 수 있다는
    우려에 대응(2026-07-14) — 자동 다운로드/재업로드 대신 '고르는 단계'를 강화해
    임베드 방식(저작권 안전)을 유지하면서 관련성 통제력을 확보한다."""
    if not items:
        return None
    listing = "\n".join(
        f"{i}. 제목: {it['snippet'].get('title', '')} / 채널: {it['snippet'].get('channelTitle', '')} / "
        f"설명: {it['snippet'].get('description', '')[:80]}"
        for i, it in enumerate(items)
    )
    prompt = f"""아래는 "{celeb_name} {topic}"으로 YouTube를 검색한 결과 목록입니다.

이 중 {celeb_name} 본인이 실제로 등장할 가능성이 높고 출처가 신뢰할 만한(공식/소속사
채널, 방송사, 뉴스, 인터뷰 등) 영상을 하나 고르세요. 아래에 해당하면 제외하세요:
- 동명이인이거나 셀럽 본인 등장 여부가 불확실한 경우
- 팬 편집·리액션·커버·쇼츠 짜깁기처럼 셀럽 본인이 직접 나오지 않는 콘텐츠
- 낚시성 제목으로 실제 내용과 무관해 보이는 경우
적합한 영상이 하나도 없으면 has_good_match를 false로 하세요.

검색 결과:
{listing}"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            tools=[_PICK_VIDEO_TOOL],
            tool_choice={"type": "tool", "name": "pick_relevant_video"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                result = block.input
                if not result.get("has_good_match"):
                    logger.info(f"[celeb] YouTube 후보 중 적합한 영상 없음 판정: {result.get('reason', '')}")
                    return None
                idx = result.get("chosen_index", -1)
                if 0 <= idx < len(items):
                    return items[idx]
        return None
    except Exception as e:
        logger.warning(f"[celeb] YouTube 결과 선별 실패: {e}")
        return None


def fetch_oembed_html(post_url: str) -> str | None:
    """Instagram 공개 게시물의 oEmbed HTML 임베드 코드를 가져온다.
    2026-06-15부터 토큰 없이 호출 가능 확인됨(공식 문서: developers.facebook.com/
    docs/instagram-platform/oembed/, blog/post/2026/06/15/tokenless-access...).
    실패(비공개 계정·삭제된 게시물 등) 시 None 반환 — 파이프라인은 이 자산만
    건너뛰고 계속 진행한다(전체를 막지 않음)."""
    params = urllib.parse.urlencode({"url": post_url})
    url = f"{IG_OEMBED_URL}?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        html = data.get("html")
        if not html:
            logger.warning(f"[celeb] oEmbed 응답에 html 없음: {post_url}")
            return None
        return html
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.warning(f"[celeb] oEmbed 실패 {e.code}: {post_url} — {body[:200]}")
        return None
    except Exception as e:
        logger.warning(f"[celeb] oEmbed 요청 실패: {post_url} — {e}")
        return None

def _youtube_search_once(query: str) -> list[dict]:
    """search.list 1회 호출. 실패·결과없음이면 빈 리스트."""
    params = urllib.parse.urlencode({
        "part": "snippet",
        "q": query[:100],
        "type": "video",
        "maxResults": 5,
        "order": "relevance",
        "regionCode": "KR",
        "relevanceLanguage": "ko",
        "key": YOUTUBE_API_KEY,
    })
    url = f"{YOUTUBE_SEARCH_URL}?{params}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.warning(f"[celeb] YouTube 검색 실패 {e.code}: {body[:200]}")
        return []
    except Exception as e:
        logger.warning(f"[celeb] YouTube 검색 요청 실패: {e}")
        return []
    return data.get("items", [])


def get_youtube_embed(celeb_name: str, topic: str = "") -> dict | None:
    """YouTube Data API v3 search.list로 셀럽 관련 영상을 자동 검색한다.
    다운로드/재호스팅 없이 video URL만 반환 — 실제 임베드는 발행 시점에
    naver_blog_poster._type_body()가 URL을 본문에 타이핑해 네이버 자동 변환
    기능으로 처리한다(정식 재생 플레이어, 2026-07-14 실측 확인).

    검색어 전략(2026-07-14 리서치로 원인 확정 후 개선): 예전엔 블로그 헤드라인
    문구(topic)를 그대로 검색어에 넣었는데, YouTube search.list는 의미 기반이
    아니라 키워드 매칭이라 실제 영상 제목과 겹치는 단어가 거의 없어 매칭 실패율이
    높았다("이성민 10kg 감량으로 달라진 비주얼" 같은 문구로 검색하면 대부분
    0건). 대신 짧고 흔한 쿼리부터 순서대로 시도하고, 결과가 있으면 그 자리에서
    멈춘다(쿼터 절약) — ["{name} 인터뷰", "{name} 근황", "{name}"].
    같은 날 이미 전부 실패한 이름은 24시간 네거티브 캐시로 재시도를 건너뛴다
    (스케줄러가 하루 3번 도는데 같은 후보가 재등장할 때 쿼터를 또 쓰지 않기 위함).
    일 쿼터 100회(search.list 1회=100 units, 기본 10,000 units/day) 한도."""
    if not YOUTUBE_API_KEY:
        logger.warning("[celeb] YOUTUBE_API_KEY 없음 — YouTube 임베드 건너뜀")
        return None

    if db.is_youtube_search_negative_cached(celeb_name):
        logger.info(f"[celeb] YouTube 네거티브 캐시 히트 — 검색 건너뜀: {celeb_name}")
        return None

    query_chain = [f"{celeb_name} 인터뷰", f"{celeb_name} 근황", celeb_name]
    items: list[dict] = []
    used_query = ""
    for query in query_chain:
        items = _youtube_search_once(query)
        if items:
            used_query = query
            break

    if not items:
        logger.info(f"[celeb] YouTube 검색 결과 없음(쿼리 {len(query_chain)}개 전부 실패): {celeb_name}")
        db.add_youtube_search_negative_cache(celeb_name)
        return None

    video = _pick_best_youtube_result(celeb_name, topic, items)
    if not video:
        logger.info(f"[celeb] YouTube 검색 결과 {len(items)}개('{used_query}') 중 적합한 영상 없음 — 건너뜀: {celeb_name}")
        return None

    video_id = video["id"].get("videoId")
    if not video_id:
        return None
    title = video["snippet"].get("title", "")
    channel = video["snippet"].get("channelTitle", "")
    return {
        "type": "embed",
        "ref": f"https://www.youtube.com/watch?v={video_id}",
        "license_note": f"YouTube 공식 임베드 — {channel} 채널 '{title}'",
        "alt_text": f"{celeb_name} 관련 영상: {title}"[:80],
    }


def _serper_search(query: str, num: int = 5) -> dict | None:
    """Serper.dev 일반 검색(구글 organic 결과) 1회 호출. 실패 시 None."""
    if not SERPER_API_KEY:
        return None
    try:
        req = urllib.request.Request(
            SERPER_SEARCH_URL,
            data=json.dumps({"q": query, "num": num}).encode("utf-8"),
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"[celeb] Serper 검색 실패: {e}")
        return None


def get_instagram_post_via_search(celeb_name: str) -> dict | None:
    """Serper.dev(구글 검색 프록시)로 site:instagram.com/p/ 검색해 게시물 URL을
    자동 발견한다(2026-07-14: 네이버 자체 검색은 프로필 URL만 찾고 게시물
    URL은 못 찾음 확인 — 프로필 URL은 네이버 에디터에서 카드로 변환되지 않는다).

    ⚠️ 예전엔 Google Custom Search JSON API를 썼으나, 2026년 1월부로 신규
    고객에게 정책적으로 완전 차단된 것을 2026-07-14 리서치로 확정했고
    2026-07-15 실제 호출로도 403 PERMISSION_DENIED 재확인했다 — 설정 문제가
    아니라 구조적 차단이라 재시도로 해결되지 않는다. Serper.dev(구글 검색결과를
    대신 제공하는 유료/무료티어 프록시 서비스)로 교체했다. SERPER_API_KEY가
    없으면 None — 호출부(get_instagram_embed)가 instagram_watchlist 수동
    큐레이션으로 폴백한다."""
    data = _serper_search(f"site:instagram.com/p/ {celeb_name}")
    if not data:
        return None

    for item in data.get("organic", []):
        link = item.get("link", "")
        match = _IG_POST_URL_RE.match(link)
        if not match:
            continue
        # 검색 URL이 site:instagram.com/p/에 걸렸다는 것만으로는 셀럽 본인 게시물이라는
        # 보장이 없다(동명이인 계정, 팬 계정 등) — 제목·스니펫에 셀럽명이 실제로
        # 언급되는지 한 번 더 확인한다(2026-07-14, "원치 않는 사진" 우려 대응).
        combined = f"{item.get('title', '')} {item.get('snippet', '')}"
        if celeb_name not in combined:
            logger.debug(f"[celeb] Serper 결과에 '{celeb_name}' 미포함 — 건너뜀: {link}")
            continue
        post_url = match.group(0)
        return {
            "type": "embed",
            "ref": post_url,
            "license_note": "Instagram 공식 임베드 (Serper 검색으로 발견, oEmbed 규격 자동 변환)",
            "alt_text": f"{celeb_name} Instagram 게시물",
        }
    logger.info(f"[celeb] Serper 검색에서 {celeb_name} 게시물 URL 못 찾음")
    return None


# 서브태그 → Pexels 검색 키워드 (인물 없는 무드컷, "인물 제외" 파라미터가 API에
# 없어 키워드 자체를 인물 없이 나올 법한 조합으로 구성 — 2026-07-13 리서치 확인)
SUB_TAG_PEXELS_QUERY: dict[str, str] = {
    "celeb_fashion": "street fashion flatlay accessories",
    "celeb_diet": "healthy diet food flatlay",
    "celeb_beauty": "skincare cosmetics flatlay",
}

# 텍스트카드 톤 — 기존 보험 블로그(따뜻한 아이보리)와 구분되는 밝고 트렌디한 무드
_CARD_STYLE_SUFFIX = (
    " Bright, trendy, modern K-beauty/fashion magazine aesthetic — soft pink (#FFE8EC) or "
    "clean white background with vivid coral (#FF6B6B) or lavender (#B4A7F5) accent color. "
    "Rounded sans-serif typography, generous whitespace, Instagram-card-like composition."
)


def _download_image(url: str, dest_path) -> bool:
    """원격 이미지 URL을 로컬 파일로 내려받는다. naver_blog_poster._insert_image()는
    OS 네이티브 '열기' 대화상자에 실제 로컬 파일 경로를 입력해야 해서, URL을
    그대로 image_map에 넣으면 조용히 실패한다(2026-07-14 실측으로 발견한
    잠재 버그 — PEXELS_API_KEY가 계속 비어 있어서 여태 한 번도 실행된 적이
    없었을 뿐, get_context_image()가 URL을 그대로 반환하고 있었다)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(data)
        return True
    except Exception as e:
        logger.warning(f"[celeb] 이미지 다운로드 실패: {url} — {e}")
        return False


def _pexels_search(query: str, per_page: int = 3) -> list[dict]:
    if not PEXELS_API_KEY:
        logger.warning("[celeb] PEXELS_API_KEY 없음 — 맥락 이미지 건너뜀")
        return []
    params = urllib.parse.urlencode({"query": query, "per_page": per_page, "orientation": "landscape"})
    url = f"{PEXELS_SEARCH_URL}?{params}"
    req = urllib.request.Request(url, headers={"Authorization": PEXELS_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("photos", [])
    except Exception as e:
        logger.warning(f"[celeb] Pexels 검색 실패: {e}")
        return []


def get_context_image(sub_tags: list[str], images_dir=None) -> dict | None:
    """서브태그에 맞는 인물 없는 맥락 이미지 1장을 Pexels에서 검색해 로컬에
    내려받는다. 반환: {"type": "context", "ref": 로컬경로, ...} 또는 None."""
    query = SUB_TAG_PEXELS_QUERY.get(sub_tags[0] if sub_tags else "", "lifestyle flatlay")
    photos = _pexels_search(query)
    if not photos:
        return None
    photo = photos[0]
    images_dir = images_dir or (DATA_DIR / "celeb_images")
    dest = images_dir / f"pexels_{photo['id']}.jpg"
    if not _download_image(photo["src"]["large"], dest):
        return None
    return {
        "type": "context",
        "ref": str(dest),
        "license_note": f"Pexels — Photo by {photo.get('photographer', 'Unknown')} on Pexels",
        "alt_text": query,
    }


def get_wikimedia_photo(celeb_name: str, images_dir=None) -> dict | None:
    """Wikidata에서 셀럽 항목을 찾아 P18(대표 이미지) 속성이 있으면 Wikimedia
    Commons에서 실제 라이선스가 명시된 실사 사진을 내려받는다(2026-07-14 신설
    — Google CSE가 정책적으로 완전 차단된 것을 확인한 뒤 리서치로 발견한 대안).

    Commons 이미지는 CC-BY/CC0 등 재사용이 명시적으로 허가된 라이선스라
    "다운로드·재업로드 금지" 원칙(저작권 미상 콘텐츠 무단 사용 방지가 취지)과
    충돌하지 않는다 — 오히려 라이선스·저작자를 본문에 명시하는 조건부 재사용이
    Commons의 설계 목적 그 자체다. 다만 위키피디아 등재 기준상 커버리지가
    톱스타·기성 배우 위주로 편중돼 있어(실측: 이성민 있음, 강재준 없음) 모든
    후보에서 나오진 않는다 — 없으면 조용히 None."""
    try:
        search_url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
            "action": "wbsearchentities", "search": celeb_name, "language": "ko",
            "format": "json", "type": "item", "limit": 3,
        })
        req = urllib.request.Request(search_url, headers={"User-Agent": "CelebBlogBot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            search_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"[celeb] Wikidata 검색 실패: {e}")
        return None

    for result in search_data.get("search", []):
        qid = result.get("id")
        if not qid:
            continue
        try:
            entity_url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
            req = urllib.request.Request(entity_url, headers={"User-Agent": "CelebBlogBot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                entity_data = json.loads(resp.read().decode("utf-8"))
            p18 = entity_data["entities"][qid]["claims"].get("P18")
            if not p18:
                continue
            filename = p18[0]["mainsnak"]["datavalue"]["value"]

            info_url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
                "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
                "iiprop": "url|extmetadata", "format": "json",
            })
            req = urllib.request.Request(info_url, headers={"User-Agent": "CelebBlogBot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                info_data = json.loads(resp.read().decode("utf-8"))
            pages = info_data["query"]["pages"]
            page = next(iter(pages.values()))
            imageinfo = page.get("imageinfo", [{}])[0]
            image_url = imageinfo.get("url")
            if not image_url:
                continue
            meta = imageinfo.get("extmetadata", {})
            artist = meta.get("Artist", {}).get("value", "Unknown")
            # HTML 태그가 섞여 오는 경우가 있어 제거
            artist = re.sub(r"<[^>]+>", "", artist).strip()
            license_name = meta.get("LicenseShortName", {}).get("value", "CC")

            images_dir_ = images_dir or (DATA_DIR / "celeb_images")
            dest = images_dir_ / f"wikimedia_{qid}.jpg"
            if not _download_image(image_url, dest):
                continue
            logger.info(f"[celeb] Wikimedia Commons 실사 사진 확보: {celeb_name} ({license_name})")
            return {
                "type": "official",
                "ref": str(dest),
                "license_note": f"Wikimedia Commons — {license_name} — 저작자: {artist}",
                "alt_text": f"{celeb_name} 사진 (Wikimedia Commons)",
            }
        except Exception as e:
            logger.debug(f"[celeb] Wikidata 항목 {qid} 처리 실패: {e}")
            continue

    logger.info(f"[celeb] Wikimedia Commons에 {celeb_name} 실사 사진 없음")
    return None


def _naver_image_search(query: str, display: int = 20) -> list[dict]:
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        logger.warning("[celeb] NAVER_CLIENT_ID/SECRET 없음 — 이미지 검색 건너뜀")
        return []
    params = urllib.parse.urlencode({"query": query, "display": display, "filter": "large"})
    url = f"{NAVER_IMAGE_SEARCH_URL}?{params}"
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_CLIENT_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_CLIENT_SECRET)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("items", [])
    except Exception as e:
        logger.warning(f"[celeb] 네이버 이미지 검색 실패: {e}")
        return []


def _serper_image_search(query: str, num: int = 15) -> list[dict]:
    """Serper.dev로 구글 이미지 검색 결과를 가져와 _naver_image_search()와
    같은 {"link": 이미지URL} 형태로 맞춘다(2026-07-15 신설 — "네이버 이미지
    검색만으로는 원하는 사진이 잘 안 나온다"는 사용자 피드백 대응). 형태를
    맞춰두면 get_downloaded_photos()의 다운로드·Vision필터·라운드로빈 로직을
    소스 구분 없이 그대로 재사용할 수 있다. SERPER_API_KEY 미설정 시 빈 리스트
    — 호출부는 네이버 결과만으로 계속 진행한다(발행을 막지 않음).

    2026-07-16 실측 발견: 인스타그램 이미지 결과의 imageUrl은
    lookaside.instagram.com/seo/google_widget/crawler/? 형태인데, 이 URL을
    직접 요청하면 실제 이미지 바이트가 아니라 HTML 래퍼 페이지가 온다(직접
    확인). 이러면 다운로드 자체는 "성공"해서 넘어가지만 Vision 필터에 사람
    사진 대신 HTML을 들이미는 셈이라 사실상 항상 실패한다. lookaside 도메인일
    때는 Serper가 같이 주는 thumbnailUrl(구글 자체 캐시, gstatic.com — 항상
    실제 이미지 바이트로 응답 확인)로 대체한다. 해상도는 원본보다 작지만
    (실측 예: 399x501) 실제로 받아지는 사진이 없는 것보다 낫다."""
    if not SERPER_API_KEY:
        return []
    try:
        req = urllib.request.Request(
            SERPER_IMAGE_SEARCH_URL,
            data=json.dumps({"q": query, "num": num}).encode("utf-8"),
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = []
        for it in data.get("images", []):
            link = it.get("imageUrl", "")
            if "lookaside.instagram.com" in link and it.get("thumbnailUrl"):
                link = it["thumbnailUrl"]
            if link:
                results.append({"link": link})
        return results
    except Exception as e:
        logger.warning(f"[celeb] Serper 이미지 검색 실패: {e}")
        return []


def _serper_instagram_photo_search(query: str, num: int = 10) -> list[dict]:
    """site:instagram.com으로 제한한 Serper 이미지 검색 — 게시물 URL을 몰라도
    인스타그램에 실제 게시된 사진 자체를 후보로 확보한다(2026-07-15 신설).
    oEmbed 임베드(get_instagram_post_via_search)와는 별개 경로: 여기서 나온
    사진은 다운로드된 뒤 get_downloaded_photos()의 기존 Claude Vision
    품질·정체성·관련성 필터를 그대로 통과해야 채택된다 — 소스가 인스타그램이든
    네이버든 검증 절차는 동일하다."""
    return _serper_image_search(f"site:instagram.com {query}", num=num)


_WATERMARK_CHECK_TOOL = {
    "name": "check_photo",
    "description": "사진이 특정 인물 1인을 깨끗하게 담은, 텍스트·워터마크 없는 실사 사진인지, 그리고 지정된 시각적 특징을 실제로 보여주는지 판단한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "shows_person": {"type": "boolean", "description": "실제 사람 사진인가(그래픽·로고·무관한 사물 아님)"},
            "is_single_focus": {
                "type": "boolean",
                "description": "지정된 인물 1명이 사진의 명확한 주인공인가. 여러 명이 함께 나온 그룹샷·콜라주는 false(설령 그중 1명이 지정 인물이어도 false).",
            },
            "has_overlay": {
                "type": "boolean",
                "description": "스톡사진 워터마크, 팬사이트/매체 로고, 캡션·생년·이름표 같은 텍스트 오버레이, 밈(meme) 편집 요소가 하나라도 보이는가",
            },
            "identity_mismatch": {
                "type": "boolean",
                "description": "사진 속 인물이 지정된 셀럽일 수 없다는 명백한 근거가 있는가(예: 성별이 명백히 다름, 확실히 다른 연령대, 인물 특징이 전혀 안 맞음). 확신이 없으면(사진 화질이 낮거나 얼굴이 잘 안 보이는 경우) false로 두되, 성별처럼 명백히 모순되는 단서가 보이면 반드시 true.",
            },
            "content_description": {
                "type": "string",
                "description": "이 사진에 실제로 무엇이 나오는지 1문장으로 사실적으로 묘사(착장·자세·배경 등, 검색어를 반복하지 말 것)",
            },
            "matches_visual_claim": {
                "type": "boolean",
                "description": "제시된 '확인해야 할 시각적 특징'을 이 사진이 실제로 보여주는가. 시각적 특징이 제시되지 않았으면 항상 false.",
            },
            "reason": {"type": "string"},
        },
        "required": ["shows_person", "is_single_focus", "has_overlay", "identity_mismatch", "content_description", "matches_visual_claim", "reason"],
    },
}


def _check_photo_quality(
    image_bytes: bytes, celeb_name: str, visual_claim: str = "", media_type: str = "image/jpeg"
) -> dict:
    """Claude Vision으로 사진이 특정 인물 1인을 깨끗하게(콜라주·텍스트 오버레이
    없이) 담은 실사 사진인지 판단한다. 2026-07-14 실측으로 발견한 문제 대응 —
    "워터마크 없음" 기준만으로는 여러 멤버가 섞인 팬메이드 생년 라벨 콜라주 같은
    것도 통과시켜서, "그 셀럽 개인" 기준을 명시적으로 추가했다.

    2026-07-15 확장 — "등근육이 핵심 키워드인 글인데 등근육 사진이 하나도
    없다"는 실측 문제 대응(닉쿤 사례: 다운로드된 4장이 무대의상·정장·마스크
    셀카뿐이었음에도 품질 필터는 전부 통과시켰다 — "깨끗한 단독샷인가"만
    보고 "주제와 실제로 맞는가"는 안 봤기 때문). visual_claim이 주어지면 같은
    Vision 호출에서 "이 사진이 그 모습을 실제로 보여주는가"도 함께 판단한다
    (matches_visual_claim) — usable 판정(하드 게이트)과는 별개의 소프트 신호로,
    get_downloaded_photos()가 관련성 우선순위 정렬에 사용한다.
    content_description은 검색 쿼리 문자열 대신 실제 alt_text로 쓴다.

    2026-07-15 재확장 — 같은 회차 실측에서 더 심각한 문제 발견: 네이버 이미지
    검색이 "닉쿤"(남성) 검색어에 완전히 무관한 여성 사진 4장을 반환했는데,
    그중 한 장이 matches_visual_claim=True(몸에 붙는 옷을 입어 몸매가 드러남)로
    잘못 통과해 hero(대표이미지)로 승격되는 사고가 실제로 재현됨 — "관련성"만
    보고 "신원"은 전혀 검증하지 않은 결과. identity_mismatch 필드를 추가해
    성별처럼 명백히 모순되는 단서가 보이면 usable 판정 자체를 차단한다(완벽한
    얼굴 인식은 아니지만, 명백한 오매칭은 이렇게라도 걸러야 함).

    반환: {"usable": bool, "matches_visual_claim": bool, "content_description": str,
    "reason": str}. 판단 실패 시 보수적으로 usable=False."""
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        claim_instruction = (
            f"\n\n또한 이 사진이 다음 모습을 실제로 보여주는지 확인하세요: \"{visual_claim}\""
            if visual_claim else
            "\n\nmatches_visual_claim은 항상 false로 반환하세요(확인할 시각적 특징이 제시되지 않음)."
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            tools=[_WATERMARK_CHECK_TOOL],
            tool_choice={"type": "tool", "name": "check_photo"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {
                        "type": "text",
                        "text": (
                            f"이 사진이 '{celeb_name}' 1인을 깨끗하게 담은 사진인지 확인하세요. "
                            f"사진 속 인물의 성별·연령대 등 눈에 보이는 특징이 '{celeb_name}'과 "
                            f"명백히 모순되면(예: 셀럽이 남성인데 사진 속 인물이 명확히 여성인 경우) "
                            f"identity_mismatch를 true로 설정하세요 — 검색엔진이 이름만 보고 무관한 "
                            f"인물 사진을 잘못 가져오는 경우가 실제로 있습니다.{claim_instruction}"
                        ),
                    },
                ],
            }],
        )
        for block in resp.content:
            if block.type == "tool_use":
                result = block.input
                usable = (
                    bool(result.get("shows_person"))
                    and bool(result.get("is_single_focus"))
                    and not bool(result.get("has_overlay"))
                    and not bool(result.get("identity_mismatch"))
                )
                return {
                    "usable": usable,
                    "matches_visual_claim": bool(result.get("matches_visual_claim")) if visual_claim else False,
                    "content_description": result.get("content_description", ""),
                    "reason": result.get("reason", ""),
                }
        return {"usable": False, "matches_visual_claim": False, "content_description": "", "reason": "판단 실패"}
    except Exception as e:
        logger.warning(f"[celeb] 사진 품질 판단 실패: {e}")
        return {"usable": False, "matches_visual_claim": False, "content_description": "", "reason": str(e)}


_VISUAL_CLAIM_TOOL = {
    "name": "extract_visual_claim",
    "description": "글 주제에서 사진으로 검증 가능한 구체적 시각 묘사를 추출한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "has_visual_claim": {
                "type": "boolean",
                "description": "주제에 사진으로 확인 가능한 구체적 시각 요소가 있는가(신체 부위·자세·착장·배경 등)",
            },
            "visual_claim": {
                "type": "string",
                "description": "사진 속에서 실제로 확인해야 할 구체적 모습 1문장. has_visual_claim이 false면 빈 문자열.",
            },
        },
        "required": ["has_visual_claim", "visual_claim"],
    },
}


def _extract_visual_claim(topic: str, sub_tags: list[str]) -> str:
    """글 주제를 사진으로 검증 가능한 구체적 시각 묘사 한 문장으로 변환한다
    (2026-07-15 신설 — "등근육이 핵심 훅인데 등근육 사진이 하나도 없다"는 실측
    문제 대응). 이 문장은 _check_photo_quality()에 함께 전달돼, 다운로드된
    사진이 실제로 이 모습을 보여주는지 Claude Vision이 판단하는 기준이 된다.
    검증 가능한 시각 요소가 없는 주제(단순 근황 등)면 빈 문자열을 반환해
    관련성 채점 자체를 건너뛰게 한다(하위 호환 — 기존처럼 품질 필터만 적용)."""
    if not topic:
        return ""
    sub_tag_str = ", ".join(sub_tags) if sub_tags else "일반"
    prompt = f"""글 주제: {topic}
카테고리: {sub_tag_str}

이 주제를 다루는 글에서 쓸 사진이, "정말 이 모습을 담고 있는지" 검증하려 합니다.
사진 속에서 실제로 확인해야 할 구체적인 시각적 모습을 한 문장으로 표현하세요
(신체 부위·자세·착장·배경 등 눈으로 판별 가능한 요소만 — 감정·서사·의미 부여는 제외).

예시:
- "3일간 계란만 먹고 짐승돌 몸매 복귀" → "상의를 벗거나 몸에 붙는 옷을 입어 등/복근 근육이 드러난 모습"
- "OO 시상식에서 레드카펫 룩 공개" → "시상식 레드카펫에서 특정 드레스/정장을 입고 포즈를 취한 모습"
- "OO 근황 포착"처럼 구체적 시각 요소가 없으면 has_visual_claim=false

JSON으로만 반환하세요."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            tools=[_VISUAL_CLAIM_TOOL],
            tool_choice={"type": "tool", "name": "extract_visual_claim"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                result = block.input
                if result.get("has_visual_claim") and result.get("visual_claim"):
                    claim = result["visual_claim"]
                    logger.info(f"[celeb] 시각적 후킹 요소 추출: {claim}")
                    return claim
                return ""
    except Exception as e:
        logger.warning(f"[celeb] 시각적 후킹 요소 추출 실패: {e}")
    return ""


_IMAGE_QUERY_TOOL = {
    "name": "make_image_queries",
    "description": "글 주제에 맞는 네이버 이미지 검색 쿼리를 만든다",
    "input_schema": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "각 쿼리는 검색창에 그대로 넣을 검색어 문자열",
                "minItems": 1,
                "maxItems": 4,
            },
        },
        "required": ["queries"],
    },
}


def _generate_image_queries(celeb_name: str, topic: str, sub_tags: list[str]) -> list[str]:
    """글 주제에 맞는 이미지 검색 쿼리를 만든다(2026-07-14 신설 — "셀럽 이름만으로
    검색하면 안 된다"는 사용자 지적 대응). 다이어트 주제면 "이전"과 "최근/변화 후"
    두 각도로 나눠 비교가 가능하게 하고, 행사·시사회 주제면 그 행사명을, 패션·뷰티
    주제면 언급된 스타일·아이템을 쿼리에 반영한다. 실패 시 셀럽명 단독 쿼리로 폴백."""
    sub_tag_str = ", ".join(sub_tags) if sub_tags else "일반"
    prompt = f"""셀럽명: {celeb_name}
글 주제: {topic}
카테고리: {sub_tag_str}

이 글에 실제로 어울리는 사진을 네이버 이미지 검색으로 찾기 위한 검색어를 1~4개 만드세요.

규칙:
- 셀럽 이름만 넣지 말고 주제와 관련된 구체적 키워드를 반드시 포함하세요.
- 카테고리가 celeb_diet(다이어트)면 두 개로 나누세요: 하나는 "다이어트 전/과거" 같은
  이전 모습을 찾는 쿼리, 다른 하나는 "다이어트 후/최근/바디프로필/몸매" 등 변화가
  드러나는 최근 모습을 찾는 쿼리 — 두 쿼리를 합치면 전후 비교가 되게 하세요.
- 주제에 특정 행사·시사회·시상식·화보 등 고유명사가 있으면 그 명칭을 쿼리에 포함하세요.
- 카테고리가 celeb_fashion/celeb_beauty면 주제에 언급된 구체적 스타일·아이템·상황을
  쿼리에 포함하세요(예: 특정 브랜드, 헤어스타일, 시상식명 등).

JSON으로만 반환하세요."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            tools=[_IMAGE_QUERY_TOOL],
            tool_choice={"type": "tool", "name": "make_image_queries"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                queries = block.input.get("queries", [])
                if queries:
                    logger.info(f"[celeb] 이미지 검색 쿼리 생성: {queries}")
                    return queries[:4]
    except Exception as e:
        logger.warning(f"[celeb] 이미지 쿼리 생성 실패: {e}")
    return [f"{celeb_name} {topic}"[:80]] if topic else [celeb_name]


_FALLBACK_QUERY_TOOL = {
    "name": "make_fallback_queries",
    "description": "시각적 특징을 짧고 흔한 검색 키워드로 변환한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1~2개의 짧은 단일/이중 단어 키워드(예: '탈의', '바디프로필', '수영장')",
                "minItems": 1,
                "maxItems": 2,
            },
        },
        "required": ["queries"],
    },
}


def _generate_fallback_queries(celeb_name: str, visual_claim: str) -> list[str]:
    """1차 검색에서 시각적으로 일치하는 사진을 못 찾았을 때, 더 짧고 흔한
    키워드로 재시도하기 위한 쿼리를 만든다(2026-07-15 신설). 네이버
    이미지검색은 길고 서술적인 구문(예: "등근육 피크 최근")보다 짧고 흔한
    단일 키워드(예: "탈의", "바디프로필")에서 관련 이미지를 찾을 확률이 더
    높다는 게 A단계 실측으로 확인됐다(네이버 이미지검색이 이미지 주변 텍스트
    기반 색인이라, 흔한 키워드일수록 그런 캡션이 달린 사진이 많기 때문)."""
    prompt = f"""다음 시각적 특징을 사진으로 찾고 싶은데, 검색이 잘 안 됩니다:
"{visual_claim}"

이 특징을 대표하는 짧고 흔한 검색 키워드를 1~2개 만드세요(단일 단어나 짧은
구, 예: "탈의", "바디프로필", "수영장" — 셀럽 이름은 붙이지 마세요, 검색 시
자동으로 함께 붙습니다).

JSON으로만 반환하세요."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            tools=[_FALLBACK_QUERY_TOOL],
            tool_choice={"type": "tool", "name": "make_fallback_queries"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                keywords = block.input.get("queries", [])
                return [f"{celeb_name} {kw}" for kw in keywords[:2]]
    except Exception as e:
        logger.warning(f"[celeb] 폴백 쿼리 생성 실패: {e}")
    return []


def get_downloaded_photos(
    celeb_name: str, topic: str = "", sub_tags: list[str] | None = None,
    max_count: int = 4, images_dir=None,
) -> list[dict]:
    """네이버 이미지 검색으로 셀럽 실사 사진 후보를 모아 다운로드하고, Claude
    Vision으로 워터마크·로고 있는 사진과 사람이 안 나온 사진을 걸러낸다.
    최대 max_count장까지 반환 — 최종 필터 통과분이 적으면 그만큼만 반환한다.

    2026-07-14: 검색 쿼리를 글 주제 기반으로 생성하도록 개선(이전엔 셀럽명+
    "instagram" 고정 쿼리 하나만 써서 "시사회 글인데 시사회 사진이 없다",
    "다이어트 글인데 전후비교가 안 된다" 같은 문제가 있었다). 여러 쿼리를
    순회하며 결과를 모으고, 특정 쿼리 하나가 결과를 독식하지 않도록 쿼리별로
    번갈아가며 최종 max_count장을 채운다.

    2026-07-14 신설(사용자 명시적 요청으로 원칙 ② 해제) — 다운로드/재업로드
    금지 원칙을 깨고 직접 이미지를 가져오는 유일한 경로. license_note에
    출처 도메인을 남겨 추적 가능성만 유지한다.

    2026-07-15 확장 — "검색 쿼리는 주제에 맞았는데 실제 사진은 무관했다"는
    실측 문제 대응(닉쿤 사례: "등근육" 쿼리로도 무대의상·정장·마스크셀카만
    나옴 — 네이버 이미지검색이 텍스트 색인 기반이라 시각적 내용을 매칭 못
    하는 구조적 한계). _extract_visual_claim()으로 "실제로 확인해야 할 모습"을
    뽑아 각 후보 사진에 matches_visual_claim 태깅하고, 그 태그가 True인 사진을
    결과 리스트 맨 앞으로 정렬해 반환한다(호출자가 대표이미지를 고를 때
    앞쪽을 우선하도록). 1차 쿼리에서 시각적 매칭이 하나도 없으면
    _generate_fallback_queries()로 짧은 키워드 재검색을 1회 더 시도한다."""
    images_dir = images_dir or (DATA_DIR / "celeb_images")
    queries = _generate_image_queries(celeb_name, topic, sub_tags or [])
    visual_claim = _extract_visual_claim(topic, sub_tags or [])

    seen_links: set[str] = set()
    results: list[dict] = []
    total_candidates = 0

    def _search_all_sources(query: str) -> list[dict]:
        """쿼리 하나에 대해 네이버+구글(Serper)+인스타그램(Serper) 후보를 전부
        모아 하나의 풀로 합친다(2026-07-15 신설 — "네이버 이미지 검색만으로는
        원하는 사진이 잘 안 나온다"는 사용자 피드백 대응). SERPER_API_KEY가
        없으면 뒤 두 소스는 조용히 빈 리스트를 반환해 기존 네이버 단일 소스
        동작과 동일하게 유지된다."""
        items = list(_naver_image_search(query, display=15))
        items += _serper_image_search(query, num=15)
        items += _serper_instagram_photo_search(query, num=10)
        return items

    def _scan(query_list: list[str]) -> None:
        """query_list를 라운드로빈으로 소비해 results/seen_links에 누적한다.
        특정 쿼리 하나가 결과를 독식하지 않도록 쿼리별로 번갈아가며 채운다
        (예: "다이어트 전" 쿼리 결과가 많다고 "다이어트 후" 사진이 하나도
        안 들어가는 상황 방지). 쿼리별 풀 자체가 이미 네이버/구글/인스타그램
        소스를 합친 것이라, 소스 간 배분도 자연히 섞인다."""
        nonlocal total_candidates
        query_pools = [(q, _search_all_sources(q)) for q in query_list]
        query_pools = [(q, items) for q, items in query_pools if items]
        if not query_pools:
            return
        total_candidates += sum(len(items) for _, items in query_pools)

        cursors = [0] * len(query_pools)
        exhausted = [False] * len(query_pools)
        while len(results) < max_count and not all(exhausted):
            for qi, (query, items) in enumerate(query_pools):
                if len(results) >= max_count:
                    break
                while cursors[qi] < len(items):
                    item = items[cursors[qi]]
                    cursors[qi] += 1
                    link = item.get("link", "")
                    if not link.startswith("http") or link in seen_links:
                        continue
                    seen_links.add(link)
                    try:
                        req = urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            content_type = resp.headers.get("Content-Type", "image/jpeg")
                            image_bytes = resp.read()
                    except Exception as e:
                        logger.debug(f"[celeb] 이미지 다운로드 실패(스킵): {link} — {e}")
                        continue
                    if len(image_bytes) < 5000:  # 너무 작은 파일(깨진 썸네일 등) 스킵
                        continue

                    check = _check_photo_quality(
                        image_bytes, celeb_name, visual_claim,
                        content_type if content_type.startswith("image/") else "image/jpeg",
                    )
                    if not check["usable"]:
                        logger.debug(f"[celeb] 사진 제외({check['reason']}): {link}")
                        continue

                    domain = urllib.parse.urlparse(link).netloc
                    dest = images_dir / f"downloaded_{celeb_name}_{len(results)}.jpg"
                    images_dir.mkdir(parents=True, exist_ok=True)
                    Path(dest).write_bytes(image_bytes)
                    results.append({
                        "type": "downloaded_photo",
                        "ref": str(dest),
                        "license_note": f"다운로드 — 검색어: {query} / 출처: {domain} (라이선스 미확인)",
                        "alt_text": check["content_description"] or f"{celeb_name} — {query}",
                        "matches_visual_claim": check["matches_visual_claim"],
                    })
                    break  # 이번 쿼리 차례에서는 1장만 소비하고 다음 쿼리로 넘어감(라운드로빈)
                else:
                    exhausted[qi] = True
                    continue
                if cursors[qi] >= len(items):
                    exhausted[qi] = True

    _scan(queries or [celeb_name])

    if visual_claim and len(results) < max_count and not any(r["matches_visual_claim"] for r in results):
        fallback_queries = _generate_fallback_queries(celeb_name, visual_claim)
        if fallback_queries:
            logger.info(f"[celeb] 시각적 매칭 폴백 재검색: {fallback_queries}")
            _scan(fallback_queries)

    if not results and total_candidates == 0:
        logger.info(f"[celeb] 다운로드용 이미지 후보 없음: {celeb_name}")

    if visual_claim and not any(r["matches_visual_claim"] for r in results):
        logger.warning(f"[celeb] 시각적 후킹 사진 없음: {celeb_name} — '{visual_claim}' 매칭 실패")

    results.sort(key=lambda r: not r["matches_visual_claim"])  # matches_visual_claim=True가 앞으로

    logger.info(
        f"[celeb] 다운로드 실사 사진 {len(results)}장 확보(후보 {total_candidates}건 중, "
        f"시각적 매칭 {sum(1 for r in results if r['matches_visual_claim'])}장): {celeb_name}"
    )
    return results


def get_instagram_embed(celeb_name: str) -> dict | None:
    """Instagram 게시물 URL을 확보한다. 우선순위:
    1. Serper.dev 자동 발견(SERPER_API_KEY 설정돼 있으면 완전 무인, 2026-07-15
       Google Custom Search에서 교체 — 그 API는 신규 고객에게 차단됨)
    2. instagram_watchlist에 사람이 미리 넣어둔 URL(폴백, 주 1회 수동 큐레이션)
    둘 다 없으면 None — 파이프라인은 계속 진행한다(정상 상황)."""
    found = get_instagram_post_via_search(celeb_name)
    if found:
        logger.info(f"[celeb] Instagram 게시물 자동 발견(Serper): {celeb_name}")
        return found

    row = db.get_unused_instagram_url(celeb_name)
    if row is None:
        return None
    db.mark_instagram_url_used(row["id"])
    return {
        "type": "embed",
        "ref": row["post_url"],
        "license_note": "Instagram 공식 임베드 (수동 큐레이션 워치리스트)",
        "alt_text": f"{celeb_name} Instagram 게시물",
    }


def get_textcard(fact_text: str, celeb_name: str, index: int, status: str = "confirmed", images_dir=None) -> dict | None:
    """핵심 팩트 1개를 카드형 텍스트 이미지로 생성한다 (blog_image_direct 재사용).

    status="unconfirmed"면 카드 문구에 "~라는 이야기가 있어요" 헤징을 붙인다
    (2026-07-15 신설 — celeb_generator.py 본문의 [미확인] 헤징과 같은 원칙을
    카드 이미지에도 적용해, 미확인 사실을 확정된 사실처럼 단정하지 않는다)."""
    images_dir = images_dir or (DATA_DIR / "celeb_images")
    images_dir.mkdir(parents=True, exist_ok=True)
    out_path = images_dir / f"card_{celeb_name}_{index}.png"

    quote = fact_text if status == "confirmed" else f"{fact_text} (아직 미확인)"
    prompt = (
        f'A minimal text card image. Large bold Korean text quote in the center: "{quote}". '
        f"No photos of people, no logos, no invented brand names — text and simple decorative "
        f"shapes/icons only." + _CARD_STYLE_SUFFIX
    )
    ok = generate_image(prompt, str(out_path), aspect_ratio="4:5")
    if not ok:
        return None
    return {
        "type": "textcard",
        "ref": str(out_path),
        "license_note": "자체 생성 (nanobanana pro / gemini-3-pro-image-preview)",
        "alt_text": quote[:50],
    }


def gather_assets(celeb_name: str, sub_tags: list[str], facts: list[dict],
                   topic: str = "", min_images: int = 2, min_real_photos: int = 6) -> list[dict]:
    """이 글에 쓸 에셋을 전부 모은다. 실사 에셋이 하나도 없어도 절대 막히지
    않는다 — Pexels+카드만으로도 항상 최소 2장은 확보된다.

    facts: hook_strength 내림차순으로 정렬된 팩트 dict 목록({"fact_text", "status", ...}).
    confirmed만이 아니라 unconfirmed도 포함해서 넘겨야 한다(2026-07-15 재설계 —
    아래 텍스트카드 참조).
    min_images: 최소 확보하려는 총 이미지 수(임베드 제외, 실제 삽입되는 이미지
    기준) — 실사 사진이 부족할 때 텍스트카드로 채우는 기준으로 쓰인다.
    min_real_photos: 다운로드 실사 사진(get_downloaded_photos) 목표 장수.
    기본 6장(2026-07-14, 사용자 요청 — "실사 사진은 6장 이상"). 후보 풀이
    부족하면 그만큼만 확보되고 조용히 넘어간다(발행을 막지 않음). Gemini
    텍스트카드 API가 불안정한 날에도 실사 사진은 이 경로로만 확보되므로
    안정적인 이미지 수 확보 수단이기도 하다.
    각 asset에 is_hero 플래그가 부여된다(_mark_hero() 참조) — 대표이미지(네이버
    본문 첫 이미지 = 검색결과/RSS 썸네일) 선정용, 실제 배치 강제는
    run_celeb_pipeline.py가 담당.
    반환된 각 dict를 celeb_db.add_asset()으로 영속화하고 그대로 반환.
    """
    assets = []

    yt = get_youtube_embed(celeb_name, topic)
    if yt:
        assets.append(yt)
        logger.info(f"[celeb] YouTube 임베드 확보: {celeb_name}")

    ig = get_instagram_embed(celeb_name)
    if ig:
        assets.append(ig)
        logger.info(f"[celeb] Instagram 임베드 확보: {celeb_name}")

    # 다운로드 실사 사진 — "실사 사진은 6장 이상"이라는 명시적 요청에 대응하는
    # 주력 경로(2026-07-14). 과도한 API 낭비 방지를 위해 상한 10장.
    photo_target = min(max(min_real_photos, 1), 10)
    photos = get_downloaded_photos(celeb_name, topic=topic, sub_tags=sub_tags, max_count=photo_target)
    assets.extend(photos)
    # 2026-07-15 확장 — 예전엔 "사진이 아예 0장일 때만" Wikimedia를 시도했는데,
    # 사용자 요청("동영상 제외 6장 고정")에 비해 몇 장이라도 모자라면 뒤이은
    # 텍스트카드(Gemini)에만 기대게 됐고, Gemini가 불안정한 날(리한나 3차
    # 발행 실측)엔 그대로 목표 미달로 이어졌다. 사진이 조금이라도 부족하면
    # Wikimedia도 보충 소스로 시도한다 — 소스가 달라 중복 위험이 낮다.
    non_embed_count = sum(1 for a in assets if a["type"] != "embed")
    if non_embed_count < min_images:
        wiki = get_wikimedia_photo(celeb_name)
        if wiki:
            assets.append(wiki)
            logger.info(f"[celeb] Wikimedia 보충 사진 확보: {celeb_name}")

    ctx = get_context_image(sub_tags)
    if ctx:
        assets.append(ctx)

    # 텍스트카드는 hook_strength가 높은 사실부터 카드화한다(2026-07-15 — 이전엔
    # confirmed_facts 순서 그대로 써서 "닉쿤은 2PM의 멤버이다" 같은 가장 밋밋한
    # 배경정보가 카드 0번이 되고, 그게 그대로 실제 발행 글의 대표이미지가 되는
    # 문제가 있었다. 호출부(run_celeb_pipeline.py)가 이미 hook_strength
    # 내림차순으로 정렬해 넘기므로, 여기서는 순서를 그대로 따르기만 하면 된다.
    #
    # 2026-07-15 재확장 — confirmed 팩트만 카드화 대상으로 삼았더니, 같은 날
    # 재설계한 celeb_facts.py의 완화된 게이트("논란 아니면 팩트 개수로 안
    # 막음")와 충돌하는 회귀가 실측됐다: confirmed가 0~1개뿐인 후보(예: 리한나)
    # 는 gather_facts는 통과하는데, 여기서 카드화할 재료가 없어 실사 사진
    # 부족분을 못 채우고 사용자가 요청한 "최소 6장" 목표에 못 미쳤다
    # (실제로 3장만 확보되고 발행됨). unconfirmed 팩트도 정당한 카드 재료다
    # — get_textcard()가 "(아직 미확인)" 헤징을 자동으로 붙여준다.
    non_embed_count = sum(1 for a in assets if a["type"] != "embed")
    cards_needed = max(min_images - non_embed_count, 0)
    card_facts = list(enumerate(facts[:max(cards_needed, 2)]))
    failed_cards = []
    for i, fact in card_facts:
        card = get_textcard(fact["fact_text"], celeb_name, i, status=fact.get("status", "confirmed"))
        if card:
            assets.append(card)
        else:
            failed_cards.append((i, fact))

    # 2026-07-15 — Gemini 503(일시적 과부하) 스파이크는 보통 몇 분 안에
    # 풀린다. run_daily_blog.py의 "누락분만 재시도" 패턴과 동일하게, 목표
    # 장수(min_images)에 못 미쳤으면 실패한 카드만 짧은 대기 후 한 번 더
    # 시도한다 — 리한나 3차 발행 실측(카드 2장 모두 503으로 실패, 사진
    # 5장으로 목표 6장 미달)에 대한 직접 대응.
    non_embed_count = sum(1 for a in assets if a["type"] != "embed")
    if failed_cards and non_embed_count < min_images:
        logger.info(f"[celeb] 텍스트카드 {len(failed_cards)}장 실패 — 10초 대기 후 재시도: {celeb_name}")
        time.sleep(10)
        for i, fact in failed_cards:
            if non_embed_count >= min_images:
                break
            card = get_textcard(fact["fact_text"], celeb_name, i, status=fact.get("status", "confirmed"))
            if card:
                assets.append(card)
                non_embed_count += 1

    _mark_hero(assets)

    for a in assets:
        db.add_asset(celeb_name, a["type"], a["ref"], a["license_note"], a["alt_text"])

    return assets


def _mark_hero(assets: list[dict]) -> None:
    """대표이미지(hero) 후보 정확히 1개에 is_hero=True를 표시한다(2026-07-15
    신설). 네이버 블로그는 본문 내 첫 번째 이미지를 검색결과·목록·RSS
    대표이미지로 자동 채택한다 — 실측(오늘 닉쿤 발행 건)으로 이 규칙을 몰라서
    생성 LLM이 배경설명 텍스트카드를 맨 앞에 배치했고, 그게 그대로 썸네일이
    돼버린 문제를 확인했다. 이 함수는 "어떤 에셋이 대표이미지가 돼야
    하는가"만 판정하고, 실제로 그걸 본문 맨 앞에 배치하는 강제는
    run_celeb_pipeline.py의 _convert_asset_markers()가 결정론적으로 수행한다
    (생성 LLM의 프롬프트 준수 여부에 기대지 않는 안전망).

    우선순위: 시각적으로 주제와 일치한다고 확인된 다운로드 실사 사진(A단계의
    matches_visual_claim) > 관련성 검증된 YouTube 임베드(_pick_best_youtube_result
    를 통과했으므로 안전한 차선책) > 관련성 무관 실사 사진(다운로드/Wikimedia) >
    텍스트카드(최후 순위, 순수 타이포그래피라 후킹력이 가장 약함)."""
    for a in assets:
        a["is_hero"] = False
    if not assets:
        return

    matched_photo = next(
        (a for a in assets if a["type"] == "downloaded_photo" and a.get("matches_visual_claim")), None
    )
    if matched_photo:
        matched_photo["is_hero"] = True
        return

    embed = next((a for a in assets if a["type"] == "embed"), None)
    if embed:
        embed["is_hero"] = True
        return

    any_photo = next((a for a in assets if a["type"] in ("downloaded_photo", "official")), None)
    if any_photo:
        any_photo["is_hero"] = True
        return

    assets[0]["is_hero"] = True


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db.init_db()

    test_facts = [
        {"fact_text": "강재준이 과거 다이어트로 30kg 이상 감량에 성공했다", "status": "confirmed"},
        {"fact_text": "요요 현상으로 다시 체중이 늘어 100kg을 넘겼다", "status": "confirmed"},
    ]
    result = gather_assets("강재준", ["celeb_diet"], test_facts)
    print(f"\n확보된 에셋 {len(result)}개:")
    for a in result:
        print(f"  [{a['type']}] {a['ref']} — {a['license_note']}")
