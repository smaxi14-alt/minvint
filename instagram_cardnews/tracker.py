"""카드뉴스 발행 이력 기록 — automation/content_tracker.py와 동일한 패턴이지만
완전히 독립된 로그 파일(instagram_cardnews/logs/cardnews_log.json)에 기록한다."""
import json
from datetime import date, datetime
from config import LOGS_DIR

LOG_FILE = LOGS_DIR / "cardnews_log.json"


def _load():
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def _save(data):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_post(queue_id: str, source_type: str, persona: str, slide_count: int, buffer_post_id: str):
    data = _load()
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "queue_id": queue_id,
        "source_type": source_type,
        "persona": persona,
        "slide_count": slide_count,
        "buffer_post_id": buffer_post_id,
    }
    data["posts"].append(entry)
    _save(data)


def was_published_today() -> bool:
    data = _load()
    today = date.today().isoformat()
    return any(p["date"] == today for p in data["posts"])


def get_recent_publish_dates(days: int = 14) -> list[str]:
    """최근 N일 발행일 목록 — selector.py의 요일 게이팅(주 3~4회)에 사용."""
    data = _load()
    cutoff = date.today().toordinal() - days
    return [
        p["date"] for p in data["posts"]
        if date.fromisoformat(p["date"]).toordinal() >= cutoff
    ]


def get_recent_source_types(days: int = 14) -> dict:
    """쓰레드/블로그 소스 비율 확인용(다양성 참고)."""
    data = _load()
    cutoff = date.today().toordinal() - days
    counts: dict[str, int] = {}
    for p in data["posts"]:
        if date.fromisoformat(p["date"]).toordinal() >= cutoff:
            st = p.get("source_type", "?")
            counts[st] = counts.get(st, 0) + 1
    return counts
