import json
from datetime import date, datetime
from config import LOGS_DIR

LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / "posts_log.json"


def _load():
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def _save(data):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_post(slot: str, content_type: str, text: str, post_id: str, phase: int, meta: dict = None):
    data = _load()
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "slot": slot,
        "content_type": content_type,
        "phase": phase,
        "post_id": post_id,
        "text": text,
    }
    if meta:
        entry["tone"] = meta.get("tone")
        entry["ending_style"] = meta.get("ending_style")
        entry["template"] = meta.get("template")
    data["posts"].append(entry)
    _save(data)


def was_posted_today(slot: str) -> bool:
    data = _load()
    today = date.today().isoformat()
    return any(p["date"] == today and p["slot"] == slot for p in data["posts"])


def get_recent_topics(days: int = 7) -> list[str]:
    data = _load()
    cutoff = date.today().toordinal() - days
    topics = []
    for p in data["posts"]:
        if date.fromisoformat(p["date"]).toordinal() >= cutoff:
            first_line = p["text"].split("\n")[0][:60]
            topics.append(first_line)
    return topics


def get_stats() -> dict:
    data = _load()
    total = len(data["posts"])
    by_phase = {}
    by_type = {}
    for p in data["posts"]:
        ph = str(p.get("phase", "?"))
        ct = p.get("content_type", "?")
        by_phase[ph] = by_phase.get(ph, 0) + 1
        by_type[ct] = by_type.get(ct, 0) + 1
    return {"total": total, "by_phase": by_phase, "by_content_type": by_type}
