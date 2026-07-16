"""
쓰레드/블로그 발행 콘텐츠를 인스타그램 카드뉴스 슬라이드 대본(텍스트)으로
변환한다. 이미지는 전혀 생성하지 않는다 — 실제 이미지는 사람이 Claude Code
세션에서 nanobanana MCP(model:"pro")로 생성한다(CLAUDE.md 고정 규칙,
session.py 참조).

2026-07-16 재구성 — 예전엔 automation/post_once.py, automation/run_daily_blog.py
안에 직접 훅을 심어 발행 직후 호출했지만, 사용자 요청("쓰레드 로직은 지금
완벽하게 구현 중이니 건드리지 말고, 인스타 자동발행은 완전히 다른 폴더로")
에 따라 그 훅을 전부 제거했다. 이제 daily_scanner.py가 자동화 파이프라인의
로그 파일(posts_log.json/blog_log.json)을 읽기 전용으로 폴링해 이 함수들을
호출한다 — automation/의 파이썬 코드를 한 줄도 import하지 않는다.
"""
import json
import re
import logging
from datetime import date, datetime

from config import DATA_DIR

logger = logging.getLogger(__name__)

QUEUE_DIR = DATA_DIR / "cardnews_queue"
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

# 페르소나 훅 분기 — brand-guidelines.md의 김부장(불안형)/박과장(합리형).
# 콘텐츠 자체에 페르소나 필드가 없으므로 본문 키워드로 판별한다.
KIM_BUJANG_KEYWORDS = ["보험료", "손해", "과다", "과보험", "부담", "줄이고"]
PARK_GWAJANG_KEYWORDS = ["포트폴리오", "수익률", "5년 전", "설계", "비교", "데이터로"]


def _detect_persona(text: str) -> str:
    kim_hits = sum(1 for kw in KIM_BUJANG_KEYWORDS if kw in text)
    park_hits = sum(1 for kw in PARK_GWAJANG_KEYWORDS if kw in text)
    if kim_hits > park_hits:
        return "kim_bujang"
    if park_hits > kim_hits:
        return "park_gwajang"
    return "common"


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _first_number(text: str) -> str:
    m = re.search(r"[0-9][0-9,.]*\s*(만원|억원|%|원|배|년|개월)", text)
    return m.group(0) if m else ""


def _renumber(slides: list[dict]) -> list[dict]:
    for i, s in enumerate(slides, start=1):
        s["slide_no"] = i
    return slides


def queue_item_path(item_id: str):
    return QUEUE_DIR / f"{item_id}.json"


def _save_queue_item(item: dict) -> None:
    path = queue_item_path(item["id"])
    path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[cardnews] 큐 저장: {path.name} ({item['source_type']}, {len(item['slides'])}장)")


def plan_from_thread(text: str, content_type: str, meta: dict, slot: str, phase: int, post_id: str) -> dict | None:
    """쓰레드 발행 본문(text) → 5~6장 카드뉴스 대본. 원문 자체가 훅 하나뿐인
    단문이라 재작성보다 압축·보강이 핵심이다."""
    try:
        sentences = _split_sentences(text)
        if len(sentences) < 2:
            logger.info(f"[cardnews] 쓰레드 문장 수 부족({len(sentences)}) — 카드뉴스 스킵")
            return None

        persona = _detect_persona(text)
        hook_number = _first_number(sentences[0]) or _first_number(text)

        slides = [
            {"slide_no": 1, "role": "HOOK", "headline": sentences[0][:40],
             "subtext": hook_number, "design_note": "빅넘버 강조, 딥네이비 배경"},
            {"slide_no": 2, "role": "PROBLEM", "headline": sentences[1][:40],
             "subtext": "", "design_note": ""},
        ]
        if len(sentences) > 2:
            slides.append({"slide_no": 3, "role": "INSIGHT", "headline": sentences[-1][:40],
                            "subtext": "", "design_note": "결론 문장 그대로"})
        if content_type in ("economy_news", "job_insight") and len(sentences) > 3:
            slides.append({"slide_no": 0, "role": "EVIDENCE", "headline": sentences[2][:40],
                            "subtext": "", "design_note": f"{content_type} 근거 보강"})

        slides.append({"slide_no": 0, "role": "CTA", "headline": "카톡 채널에서 무료 진단 신청",
                        "subtext": "이 카드 배우자에게 공유해보세요", "design_note": "오렌지 CTA 버튼"})
        _renumber(slides)

        item = {
            "id": f"{date.today().strftime('%Y%m%d')}_thread_{post_id}",
            "source_type": "thread",
            "source_ref": {"post_id": post_id, "slot": slot, "phase": phase, "content_type": content_type},
            "created_at": datetime.now().isoformat(),
            "status": "pending_selection",
            "persona": persona,
            "slides": slides,
            "caption_seed": sentences[0],
            "hashtags": [],
        }
        _save_queue_item(item)
        return item
    except Exception as e:
        logger.warning(f"[cardnews] 쓰레드 → 카드뉴스 변환 실패(무시): {e}")
        return None


def _extract_sections(body: str) -> list[dict]:
    """`## 소제목` 단위로 마크다운 본문을 분절, 각 섹션의 표 포함 여부도 반환."""
    parts = re.split(r"(?m)^##\s+(.+)$", body)
    sections = []
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        table_lines = [l for l in content.splitlines() if l.strip().startswith("|")]
        sections.append({"title": title, "content": content.strip(), "has_table": len(table_lines) >= 2})
    return sections


def _lead_paragraph(body: str) -> str:
    first_heading = re.search(r"(?m)^##\s+", body)
    lead = body[:first_heading.start()] if first_heading else body
    lead = lead.strip()
    return lead.split("\n\n")[0][:80] if lead else ""


def plan_from_blog(post: dict, url: str) -> dict | None:
    """블로그 발행 dict(post, automation/blog_generator.py의
    generate_blog_post() 반환값과 동일한 필드 구조 — 여기서는 파이썬 코드로
    import하지 않고, daily_scanner.py가 last_run_post.json/blog_log.json을
    읽어 이 dict 모양으로 재구성해 넘긴다) → 7장 카드뉴스 대본. 블로그는
    이미 `##` 소제목 4개 이상 + Before/After 표 구조라 매핑 비용이 낮다."""
    try:
        body = post.get("body", "")
        sections = _extract_sections(body)
        if not sections:
            logger.info("[cardnews] 블로그 소제목 없음 — 카드뉴스 스킵")
            return None

        persona = _detect_persona(body)
        lead = _lead_paragraph(body)

        slides = [
            {"slide_no": 1, "role": "HOOK", "headline": lead or post["title"][:40],
             "subtext": _first_number(lead), "design_note": "본문 첫 문단(두괄식 결론) 기반"},
            {"slide_no": 2, "role": "PROBLEM", "headline": sections[0]["title"],
             "subtext": "", "design_note": ""},
        ]
        rest_sorted = sorted(sections[1:], key=lambda s: not s["has_table"])
        for s in rest_sorted[:3]:
            slides.append({
                "slide_no": 0, "role": "EVIDENCE" if s["has_table"] else "INSIGHT",
                "headline": s["title"], "subtext": "",
                "design_note": "Before=레드(#C0392B)/After=그린·오렌지 빅넘버 카드" if s["has_table"] else "",
            })

        signature = (post.get("signature") or "").strip()
        if signature:
            slides.append({"slide_no": 0, "role": "DEEPER_DIVE", "headline": signature[:40],
                            "subtext": "", "design_note": "시그니처 문장 인용구"})

        slides.append({"slide_no": 0, "role": "CTA", "headline": "카카오 오픈채팅 상담",
                        "subtext": "배우자와 상의해보세요", "design_note": "오렌지 CTA 버튼"})
        _renumber(slides)

        slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", post["title"])[:24].strip("-")
        item = {
            "id": f"{date.today().strftime('%Y%m%d')}_blog_{slug}",
            "source_type": "blog",
            "source_ref": {"url": url, "series": post.get("series", ""), "title": post["title"],
                            "keyword": post.get("keyword", "")},
            "created_at": datetime.now().isoformat(),
            "status": "pending_selection",
            "persona": persona,
            "slides": slides,
            "caption_seed": lead,
            "hashtags": [],
        }
        _save_queue_item(item)
        return item
    except Exception as e:
        logger.warning(f"[cardnews] 블로그 → 카드뉴스 변환 실패(무시): {e}")
        return None
