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
        entry["theme"] = meta.get("theme")      # 다양성 추적용
        entry["is_trend"] = meta.get("is_trend", False)  # 트렌드 소재 여부
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


def get_recent_usage(days: int = 14) -> dict:
    """최근 N일 theme / template / tone 사용 빈도 반환 — 다양성 선택에 활용"""
    data = _load()
    cutoff = date.today().toordinal() - days
    themes: dict[str, int] = {}
    templates: dict[str, int] = {}
    tones: dict[str, int] = {}
    endings: dict[str, int] = {}
    for p in data["posts"]:
        if date.fromisoformat(p["date"]).toordinal() >= cutoff:
            for key, bucket in (
                ("theme",        themes),
                ("template",     templates),
                ("tone",         tones),
                ("ending_style", endings),
            ):
                val = p.get(key, "")
                if val:
                    bucket[val] = bucket.get(val, 0) + 1
    return {"themes": themes, "templates": templates, "tones": tones, "endings": endings}


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
