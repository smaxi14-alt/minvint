import json
from datetime import date, datetime
from config import LOGS_DIR

LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / "blog_log.json"


def _load():
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def _save(data):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_post(
    series: str,
    title: str,
    url: str,
    tags: list[str],
    hook_type: str = "",
    metaphor_domain: str = "",
    signature: str = "",
    elements_used: list[str] | None = None,
    keyword: str = "",
    tier: str = "",
    combo_key: str = "",
    cta_style: str = "",
):
    """발행 로그 기록. hook_type/metaphor_domain/signature/elements_used는
    _context/blog-writing-program.md의 로테이션 규칙(같은 후킹 유형·비유 분야
    연속 사용 금지) 적용을 위한 필드. keyword/tier/combo_key는
    automation/blog_keywords.py의 1일 1글 키워드 중복 방지용 필드
    (_context/naver-blog-6month-strategy.md 참조). cta_style은 PART 2-A
    CTA 마무리 구성안(8유형) 로테이션 — 같은 구성안 2회 연속 금지 규칙에 사용."""
    data = _load()
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "series": series,
        "title": title,
        "url": url,
        "tags": tags,
        "hook_type": hook_type,
        "metaphor_domain": metaphor_domain,
        "signature": signature,
        "elements_used": elements_used or [],
        "keyword": keyword,
        "tier": tier,
        "combo_key": combo_key,
        "cta_style": cta_style,
    }
    data["posts"].append(entry)
    _save(data)


def get_last_hook_type() -> str:
    """직전 글의 후킹 유형(①) — 2회 연속 동일 유형 금지 규칙에 사용."""
    data = _load()
    posts = data["posts"]
    return posts[-1].get("hook_type", "") if posts else ""


def get_recent_metaphor_domains(count: int = 2) -> list[str]:
    """최근 N개 글의 비유 분야(③) — 3회 연속 동일 분야 금지 규칙에 사용."""
    data = _load()
    posts = data["posts"][-count:] if data["posts"] else []
    return [p.get("metaphor_domain", "") for p in posts if p.get("metaphor_domain")]


def get_recent_cta_styles(count: int = 2) -> list[str]:
    """최근 N개 글의 CTA 마무리 구성안(PART 2-A) — 같은 구성안 2회 연속
    금지, 8개 순환 로테이션 적용을 위한 필드."""
    data = _load()
    posts = data["posts"][-count:] if data["posts"] else []
    return [p.get("cta_style", "") for p in posts if p.get("cta_style")]


def was_posted_this_week() -> bool:
    """이번 주(최근 7일) 이미 블로그 글을 올렸는지 확인 — 중복 발행 방지."""
    data = _load()
    cutoff = date.today().toordinal() - 7
    return any(
        date.fromisoformat(p["date"]).toordinal() >= cutoff for p in data["posts"]
    )


def was_posted_today() -> bool:
    """오늘 이미 블로그 글을 발행했는지 확인 — 1일 1글 체제에서 스케줄러
    중복 실행(재부팅 후 StartWhenAvailable 재트리거 등) 방지용."""
    data = _load()
    today = date.today().isoformat()
    return any(p["date"] == today for p in data["posts"])


def get_used_combo_keys(days: int = 400) -> set[str]:
    """최근 N일간 사용한 키워드 조합 키(location|topic|action 등) 집합.
    automation/blog_keywords.py가 완전히 같은 조합을 다시 뽑지 않도록
    제외 목록으로 사용한다. 기본 400일 = 6개월(182일) 캠페인 전체 기간을 덮는다."""
    data = _load()
    cutoff = date.today().toordinal() - days
    return {
        p["combo_key"] for p in data["posts"]
        if p.get("combo_key") and date.fromisoformat(p["date"]).toordinal() >= cutoff
    }


def get_recent_keywords(days: int = 30) -> list[str]:
    """최근 N일간 사용한 키워드 문구 목록 — 소재 반복 감지용."""
    data = _load()
    cutoff = date.today().toordinal() - days
    return [
        p["keyword"] for p in data["posts"]
        if p.get("keyword") and date.fromisoformat(p["date"]).toordinal() >= cutoff
    ]


def get_recent_tiers(days: int = 14) -> list[str]:
    """최근 N일간 사용한 키워드 티어(A/B/C) 목록 — 티어 비율 보정용."""
    data = _load()
    cutoff = date.today().toordinal() - days
    return [
        p["tier"] for p in data["posts"]
        if p.get("tier") and date.fromisoformat(p["date"]).toordinal() >= cutoff
    ]


def get_recent_series(days: int = 30) -> list[str]:
    """최근 N일간 사용한 시리즈 목록 — 다양성 확보용."""
    data = _load()
    cutoff = date.today().toordinal() - days
    return [
        p["series"] for p in data["posts"]
        if date.fromisoformat(p["date"]).toordinal() >= cutoff
    ]


def get_recent_titles(days: int = 60) -> list[str]:
    """최근 N일간 제목 목록 — 중복 소재 방지용."""
    data = _load()
    cutoff = date.today().toordinal() - days
    return [
        p["title"] for p in data["posts"]
        if date.fromisoformat(p["date"]).toordinal() >= cutoff
    ]
