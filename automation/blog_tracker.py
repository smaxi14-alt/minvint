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
):
    """발행 로그 기록. hook_type/metaphor_domain/signature/elements_used는
    _context/blog-writing-program.md의 로테이션 규칙(같은 후킹 유형·비유 분야
    연속 사용 금지) 적용을 위한 필드."""
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


def was_posted_this_week() -> bool:
    """이번 주(최근 7일) 이미 블로그 글을 올렸는지 확인 — 중복 발행 방지."""
    data = _load()
    cutoff = date.today().toordinal() - 7
    return any(
        date.fromisoformat(p["date"]).toordinal() >= cutoff for p in data["posts"]
    )


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
