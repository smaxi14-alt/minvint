"""1일 1글 무인 자동화 전용 — 직접 Gemini API 이미지 생성.

CLAUDE.md의 "이미지 생성은 반드시 mcp__nanobanana__generate_image로만" 규칙에 대한
명시적 예외다. MCP 툴은 Claude Code 세션 안에서만 호출 가능해 매일 11시 무인
발행 파이프라인에서는 쓸 수 없다 — 그렇다고 사람이 매일 이미지를 생성해줘야
한다면 "완전 자동"이 아니게 된다. 이 모듈은 nanobanana pro가 실제로 감싸고
있는 것과 **같은 모델**(gemini-3-pro-image-preview)을 google-genai SDK로 직접
호출한다. 카드뉴스·수동 세션 작업은 계속 MCP 툴을 쓴다 — 이 모듈은
automation/run_daily_blog.py 전용이다.

절대 다른 이미지 모델로 대체하지 않는다. 과거(2026-05-18, card_news_pipeline.py)
Imagen 계열(imagen-4.0-generate-001) predict API를 직접 호출했을 때 한글 텍스트가
심하게 깨졌다("건강검진 결과를 받고" → "부여한 엄안 머가능" 등) — gemini-3-pro-image-preview는
실측 검증 결과(2026-07-11) 한글 텍스트를 정확히 렌더링해 이 문제가 재현되지 않았다.

또 하나의 실측 리스크: 이 모델은 프롬프트에 없는 내용(태그라인·로고·가짜 브랜드명 등)을
임의로 추가하는 경향이 있다(오늘 nanobanana MCP·직접 API 양쪽에서 각각 한 번씩 재현됨).
사람이 매일 리뷰하지 않으므로 모든 프롬프트에 강한 금지 조항을 강제로 덧붙인다
(_NEGATIVE_SUFFIX) — 완벽한 차단은 아니며, _context/blog-daily-automation-strategy.md
§7.2의 주간 배치 감사가 최종 방어선이다.
"""
import logging
import time

from google import genai
from google.genai import types

from config import GOOGLE_API_KEY

logger = logging.getLogger(__name__)

MODEL = "gemini-3-pro-image-preview"

_NEGATIVE_SUFFIX = (
    " IMPORTANT: Render ONLY the exact Korean text explicitly quoted above — nothing else. "
    "Do NOT add any additional taglines, captions, slogans, logos, mascots, brand names, "
    "company names, or extra decorative text of any kind. Do not invent a brand or product name. "
    "Do NOT render any long paragraph of body text or dense small print — only short quoted "
    "phrases/labels and large numbers. If no exact text was quoted for an element, leave it as "
    "a plain icon/shape with no text rather than inventing filler text or checklist items."
)

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        # 2026-07-15 실측: HttpOptions.timeout 미지정 시 서버가 응답을 안 주는
        # 상황(고부하 등)에서 요청이 무한정 멈춘다 — 실제로 makeup 발행 1건이
        # 두 번째 이미지 생성 호출에서 CPU 증가 없이 10분+ 멈춰있는 것을 확인.
        # generate_image()의 재시도 루프가 작동하려면 개별 호출에 상한이 있어야
        # 한다(180초: 정상 응답은 보통 수십 초, 20분+ 지연 사례는 503으로 끝났지
        # 무응답으로 걸린 적은 없었음).
        _client = genai.Client(api_key=GOOGLE_API_KEY, http_options=types.HttpOptions(timeout=180_000))
    return _client


def generate_image(prompt: str, output_path: str, aspect_ratio: str = "4:3", max_retries: int = 2) -> bool:
    """이미지 1장을 생성해 output_path에 저장한다. 성공하면 True.

    실패해도 예외를 던지지 않는다 — 호출자(run_daily_blog.py)가 이미지 개수
    부족을 감지해 발행 여부를 판단하도록 로그만 남기고 False를 반환한다.
    """
    client = _get_client()
    full_prompt = prompt.strip() + _NEGATIVE_SUFFIX

    for attempt in range(1, max_retries + 2):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[full_prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                ),
            )
            parts = response.candidates[0].content.parts
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline is not None and inline.data:
                    with open(output_path, "wb") as f:
                        f.write(inline.data)
                    logger.info(f"이미지 생성 완료: {output_path} ({len(inline.data)} bytes, 시도 {attempt})")
                    return True
            logger.warning(f"이미지 파트 없음 (시도 {attempt}/{max_retries + 1}) — 재시도")
        except Exception as e:
            logger.warning(f"이미지 생성 실패 (시도 {attempt}/{max_retries + 1}): {e}")
        time.sleep(2)

    logger.error(f"이미지 생성 {max_retries + 1}회 모두 실패: {output_path}")
    return False


def generate_images_for_specs(image_specs: list[dict], images_dir) -> dict[int, str]:
    """blog_generator.py의 image_specs(index/style/prompt)를 받아 전부 생성하고
    {index: 저장경로} 매핑을 반환한다. 대표 이미지(index 0)는 16:9, 나머지는 4:3
    (_context/blog-style-guide.md §8 기준)."""
    images_dir.mkdir(parents=True, exist_ok=True)
    image_map: dict[int, str] = {}
    for spec in image_specs:
        idx = spec["index"]
        aspect = "16:9" if idx == 0 else "4:3"
        out_path = images_dir / f"img_{idx}.png"
        if generate_image(spec["prompt"], str(out_path), aspect_ratio=aspect):
            image_map[idx] = str(out_path)
    return image_map
