"""
유튜브 채널 스크립트 분석기

역할:
  설정된 YouTube 채널의 최신 영상 스크립트를 수집·분석해
  모든 콘텐츠 타입의 소재로 변환.

흐름:
  1. 채널 /videos 페이지에서 최신 영상 최대 5개 조회
  2. 이미 처리한 영상 제외 (youtube_cache.json)
  3. youtube-transcript-api 로 스크립트 추출
  4. Claude haiku 로 핵심 인사이트 → 전체 content_type별 소재 변환
  5. youtube_cache.json 에 당일 캐시 저장
"""

import json
import logging
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone

from config import ANTHROPIC_API_KEY, DATA_DIR
from anthropic import Anthropic

logger = logging.getLogger(__name__)

client = Anthropic(api_key=ANTHROPIC_API_KEY)

YOUTUBE_CACHE_FILE = DATA_DIR / "youtube_cache.json"

_ALL_TYPES = [
    "agro", "agro_finance", "finance", "finance_insurance",
    "job_insight", "diagnosis", "economy_news", "book_insight",
]

_TYPE_GUIDES = {
    "agro":              "40대 가장이 공감할 일상·돈·나이듦·직장 소재 (예: '금리 올라도 월급은 그대로')",
    "agro_finance":      "40대 공감 또는 가벼운 재테크 관찰 소재",
    "finance":           "재테크·자산·부채·노후 준비 관련 데이터·통계 소재",
    "finance_insurance": "재테크 또는 보험 관련 소재",
    "job_insight":       "보험설계사·재무상담사가 13년 관찰하며 느낀 인사이트 소재",
    "diagnosis":         "보험 진단·리모델링·과보험 관련 소재",
    "economy_news":      "경제뉴스 해석 소재 (금리·환율·주식·부동산·연금)",
    "book_insight":      "경제·재무 인사이트 (책 구절로 연결 가능한 소재)",
}


def _get_channel_id(handle_or_url: str) -> str | None:
    """@handle 또는 채널 URL → channel_id(UCxxx) 변환."""
    # 이미 channel_id 형식
    if re.match(r"^UC[a-zA-Z0-9_-]{22}$", handle_or_url):
        return handle_or_url
    # URL에 channel_id 포함
    match = re.search(r"channel/(UC[a-zA-Z0-9_-]{22})", handle_or_url)
    if match:
        return match.group(1)
    # @handle → 채널 페이지 파싱
    handle = handle_or_url.split("/")[-1]
    if not handle.startswith("@"):
        handle = f"@{handle}"
    url = f"https://www.youtube.com/{urllib.parse.quote(handle)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
        for pattern in [
            r'"externalId":"(UC[a-zA-Z0-9_-]{22})"',
            r'"channelId":"(UC[a-zA-Z0-9_-]{22})"',
            r'"browseId":"(UC[a-zA-Z0-9_-]{22})"',
            r'channel/(UC[a-zA-Z0-9_-]{22})',
        ]:
            match = re.search(pattern, html)
            if match:
                logger.info(f"[yt] channel_id 추출: {match.group(1)}")
                return match.group(1)
    except Exception as e:
        logger.warning(f"[yt] channel_id 추출 실패 ({handle}): {e}")
    return None


def _fetch_videos_from_page(handle_or_url: str) -> list[dict]:
    """채널 /videos 페이지 파싱으로 최신 영상 5개 반환 (RSS 불가 채널 대응)."""
    handle = handle_or_url.split("/")[-1]
    if not handle.startswith("@"):
        handle = f"@{handle}"
    url = f"https://www.youtube.com/{urllib.parse.quote(handle)}/videos"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")

        # video ID 추출 (길이 정보 직전에 나오는 패턴)
        video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
        seen: set[str] = set()
        unique_ids = []
        for vid_id in video_ids:
            if vid_id not in seen:
                seen.add(vid_id)
                unique_ids.append(vid_id)
            if len(unique_ids) >= 5:
                break

        videos = []
        for vid_id in unique_ids:
            title = _get_video_title(vid_id)
            videos.append({
                "id":    vid_id,
                "title": title,
                "url":   f"https://www.youtube.com/watch?v={vid_id}",
            })

        logger.info(f"[yt] 페이지 파싱 {len(videos)}개 영상 조회 ({handle})")
        return videos
    except Exception as e:
        logger.warning(f"[yt] 채널 페이지 조회 실패: {e}")
        return []


def _get_video_title(video_id: str) -> str:
    """영상 페이지에서 제목 추출."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8")
        m = re.search(r'<title>(.*?) - YouTube</title>', html)
        return m.group(1) if m else video_id
    except Exception:
        return video_id


def _load_cache() -> dict:
    if YOUTUBE_CACHE_FILE.exists():
        with open(YOUTUBE_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_ids": [], "last_check": "", "insights_by_type": {}, "insights_date": ""}


def _save_cache(cache: dict):
    with open(YOUTUBE_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _get_transcript(video_id: str) -> str:
    """youtube-transcript-api 로 스크립트 추출. 실패 시 빈 문자열."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        try:
            segments = api.fetch(video_id, languages=["ko", "ko-KR"])
        except Exception:
            # 자동 생성 자막 포함 전체 목록에서 시도
            tl = api.list(video_id)
            t  = next(iter(tl), None)
            if t is None:
                return ""
            segments = api.fetch(video_id, languages=[t.language_code])
        text = " ".join(seg.text for seg in segments)
        logger.info(f"[yt] 스크립트 {len(text)}자 추출 ({video_id})")
        return text[:6000]  # 토큰 절약
    except ImportError:
        logger.warning("[yt] youtube-transcript-api 미설치 — pip install youtube-transcript-api")
        return ""
    except Exception as e:
        logger.warning(f"[yt] 스크립트 추출 실패 ({video_id}): {e}")
        return ""


def _extract_insights_all_types(title: str, transcript: str) -> dict[str, list[str]]:
    """Claude haiku 로 영상 내용 → 전체 content_type별 소재 dict 추출."""
    content = f"제목: {title}\n\n스크립트:\n{transcript[:3000]}" if transcript else f"제목: {title}"
    guides_str = "\n".join(f'- "{k}": {v}' for k, v in _TYPE_GUIDES.items())

    prompt = f"""아래는 한국 경제 분석가의 유튜브 영상입니다.

이 내용에서 40대 직장인·가장이 쓰레드 SNS 글감으로 쓸 수 있는
소재를 아래 카테고리별로 각 1~2개씩 추출하세요.

카테고리 설명:
{guides_str}

조건:
- 구체적 수치나 데이터 포함 소재 우선
- 각 소재 1줄 이내 (20~40자)
- 해당 카테고리에 맞는 소재가 없으면 빈 배열
- JSON 객체로만 반환 (다른 텍스트 절대 금지)

영상 내용:
{content}

JSON 반환 예시:
{{"agro": ["소재1"], "agro_finance": ["소재2"], "finance": ["소재3", "소재4"], "finance_insurance": [], "job_insight": ["소재5"], "diagnosis": [], "economy_news": ["소재6"], "book_insight": ["소재7"]}}"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result: dict = json.loads(raw)
        validated = {}
        for t in _ALL_TYPES:
            v = result.get(t, [])
            validated[t] = v[:2] if isinstance(v, list) and all(isinstance(i, str) for i in v) else []
        return validated
    except Exception as e:
        logger.warning(f"[yt] 인사이트 추출 실패: {e}")
    return {t: [] for t in _ALL_TYPES}


def analyze_channel(channel_handle: str) -> dict[str, list[str]]:
    """
    채널의 새 영상을 분석해 content_type별 소재 dict 반환.
    이미 처리한 영상은 건너뜀 (youtube_cache.json processed_ids 기준).
    """
    videos    = _fetch_videos_from_page(channel_handle)
    cache     = _load_cache()
    processed = set(cache.get("processed_ids", []))

    new_videos = [v for v in videos if v["id"] not in processed]
    if not new_videos:
        logger.info("[yt] 새 영상 없음")
        return {}

    logger.info(f"[yt] 새 영상 {len(new_videos)}개 분석 시작")
    merged: dict[str, list[str]] = {t: [] for t in _ALL_TYPES}

    for video in new_videos[:2]:  # 최대 2개 처리 (API 비용 절약)
        logger.info(f"[yt] 분석: {video['title']}")
        transcript = _get_transcript(video["id"])
        type_insights = _extract_insights_all_types(video["title"], transcript)
        for t, themes in type_insights.items():
            merged[t].extend(themes)
        processed.add(video["id"])

    cache["processed_ids"] = list(processed)[-50:]
    cache["last_check"]    = datetime.now(timezone.utc).isoformat()
    _save_cache(cache)

    total = sum(len(v) for v in merged.values())
    logger.info(f"[yt] 완료: 전체 {total}개 소재 추출")
    return merged


def get_youtube_themes(content_type: str) -> list[str]:
    """
    모든 content_type에 대해 YouTube 소재 반환.
    하루 1회 채널 분석 후 전체 타입을 youtube_cache에 저장,
    이후 호출은 캐시에서 즉시 반환.

    우선순위:
      1차 — YOUTUBE_CHANNELS_PRIMARY (@김영익, @howtoberich_official)
      2차 — YOUTUBE_CHANNELS_FALLBACK (@hkwowtv): 1차 economy_news 소재가 PRIMARY_THRESHOLD 미만일 때만
    """
    import os
    from datetime import date as _date

    today = _date.today().isoformat()
    cache = _load_cache()

    # 오늘 이미 분석했으면 캐시에서 반환
    if cache.get("insights_date") == today:
        result = cache.get("insights_by_type", {}).get(content_type, [])
        logger.info(f"[yt] 캐시 반환 ({content_type}): {len(result)}개")
        return result

    # 새 날: processed_ids 초기화 — 같은 영상도 새로 분석해 최신 소재 반영
    prev_insights = cache.get("insights_by_type", {})
    cache["processed_ids"] = []
    _save_cache(cache)

    primary_raw  = os.getenv("YOUTUBE_CHANNELS_PRIMARY", "")
    fallback_raw = os.getenv("YOUTUBE_CHANNELS_FALLBACK", "")

    # 하위호환: 구형 YOUTUBE_CHANNELS 단일 변수도 지원
    if not primary_raw and not fallback_raw:
        legacy = os.getenv("YOUTUBE_CHANNELS", "")
        if not legacy:
            # 채널 미설정 — 전일 캐시라도 반환
            if any(prev_insights.values()):
                logger.info("[yt] 채널 미설정 — 전일 캐시 소재 사용")
                cache = _load_cache()
                cache["insights_by_type"] = prev_insights
                cache["insights_date"]    = today
                _save_cache(cache)
            return prev_insights.get(content_type, [])
        primary_raw = legacy

    PRIMARY_THRESHOLD = 3
    all_type_insights: dict[str, list[str]] = {t: [] for t in _ALL_TYPES}

    # 1차: primary 채널 분석
    for ch in [c.strip() for c in primary_raw.split(",") if c.strip()]:
        ch_insights = analyze_channel(ch)
        for t, themes in ch_insights.items():
            all_type_insights[t].extend(themes)

    # 2차: economy_news 소재 부족 시 fallback 채널 추가 분석
    if len(all_type_insights.get("economy_news", [])) < PRIMARY_THRESHOLD and fallback_raw:
        logger.info(f"[yt] 1차 economy_news {len(all_type_insights['economy_news'])}개 — fallback 채널 분석")
        for ch in [c.strip() for c in fallback_raw.split(",") if c.strip()]:
            ch_insights = analyze_channel(ch)
            for t, themes in ch_insights.items():
                all_type_insights[t].extend(themes)

    # 모든 채널에서 소재가 없으면 전일 캐시 소재로 폴백 (영상 없거나 스크립트 추출 실패 시)
    if not any(all_type_insights.values()) and any(prev_insights.values()):
        logger.info("[yt] 새 소재 없음 — 전일 캐시 소재 사용")
        all_type_insights = prev_insights

    # 당일 캐시 저장 (이후 같은 날 모든 타입 호출은 캐시 사용)
    cache = _load_cache()  # analyze_channel이 저장한 processed_ids 반영
    cache["insights_by_type"] = all_type_insights
    cache["insights_date"]    = today
    _save_cache(cache)

    return all_type_insights.get(content_type, [])
