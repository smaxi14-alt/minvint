import json
from datetime import date, datetime
from config import LOGS_DIR, today_kst

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
        "date": today_kst().isoformat(),
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
    today = today_kst().isoformat()
    return any(p["date"] == today and p["slot"] == slot for p in data["posts"])


def get_recent_topics(days: int = 7) -> list[str]:
    data = _load()
    cutoff = today_kst().toordinal() - days
    topics = []
    for p in data["posts"]:
        if date.fromisoformat(p["date"]).toordinal() >= cutoff:
            first_line = p["text"].split("\n")[0][:60]
            topics.append(first_line)
    return topics


def get_recent_post_texts(days: int = 90) -> list[dict]:
    """최근 N일 발행 글의 (date, text) 전문을 반환한다 — 새 글이 과거 글과
    같은 소재(예: "삼성전자 팔았다")를 다른 구체적 수치로 다시 쓰는 자기모순을
    막기 위한 대조 검증용(2026-07-14, "삼성전자 8만원" 오기재 → 정정 후에도
    같은 실수가 반복될 수 있다는 지적에서 신설). 전체 글 개수가 적은 초기
    운영 단계라 90일 기본값으로도 API 호출 부담이 크지 않다.

    retracted=True로 표시된 글(사용자가 라이브에서 직접 삭제한 오기재 글 등)은
    제외한다 — 더 이상 공개돼 있지 않은 글과의 "모순"은 실질적 리스크가 아니고,
    오히려 검증 체커가 이미 알려진 과거 오류와 그 정정글을 서로 모순으로 오판하게
    만든다(실측 확인됨)."""
    data = _load()
    cutoff = today_kst().toordinal() - days
    return [
        {"date": p["date"], "text": p["text"]}
        for p in data["posts"]
        if date.fromisoformat(p["date"]).toordinal() >= cutoff and not p.get("retracted")
    ]


def get_recent_usage(days: int = 14) -> dict:
    """최근 N일 theme / template / tone 사용 빈도 반환 — 다양성 선택에 활용"""
    data = _load()
    cutoff = today_kst().toordinal() - days
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
