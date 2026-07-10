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


def log_post(series: str, title: str, url: str, tags: list[str]):
    data = _load()
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "series": series,
        "title": title,
        "url": url,
        "tags": tags,
    }
    data["posts"].append(entry)
    _save(data)


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
