"""셀럽 트렌드 블로그 자동화 — SQLite 데이터 모델 + CRUD 헬퍼.

기존 보험 블로그는 blog_log.json(JSON) 기반이었지만, 이 파이프라인은 STAGE별로
서로 참조하는 테이블이 여러 개(trend_candidates/facts/assets/drafts/published/
instagram_watchlist)라 관계형 쿼리가 필요해 SQLite를 쓴다
(_context/... 계획 문서, 사용자 개발 지시서 4장 스키마 기준).

download_reupload 자산 타입은 CHECK 제약에서 아예 뺀다 — 셀럽 사진 무단
다운로드·재업로드 금지 원칙(원칙 ②)을 스키마 레벨에서부터 강제한다.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from config import DATA_DIR

DB_PATH = DATA_DIR / "celeb.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trend_candidates (
    id INTEGER PRIMARY KEY,
    celeb_name TEXT NOT NULL,
    topic TEXT,
    sub_tags TEXT,          -- JSON 배열: ["celeb_fashion", ...]
    signal_score REAL,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    celeb_name TEXT NOT NULL,
    sub_tags TEXT,           -- JSON 배열
    fact_text TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('confirmed','unconfirmed')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY,
    celeb_name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('embed','official','context','textcard','stock','downloaded_photo')),
    ref TEXT NOT NULL,       -- url 또는 로컬 경로
    license_note TEXT,
    alt_text TEXT
);
-- downloaded_photo: 2026-07-14, 사용자가 원칙 ②(다운로드·재업로드 금지)를
-- 명시적으로 해제 요청해 추가됨(임베드/Wikimedia만으로는 원하는 실사 커버리지가
-- 부족하다는 반복된 피드백에 따름). license_note에 출처(원본 도메인)를 항상
-- 남겨 최소한의 추적 가능성은 유지한다.

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    celeb_name TEXT NOT NULL,
    category TEXT,
    title_candidates TEXT,   -- JSON 배열
    body_markdown TEXT,
    hashtags TEXT,           -- JSON 배열
    asset_placements TEXT,   -- JSON
    status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK(status IN ('pending_review','ready_to_publish','blocked','published')),
    block_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS published (
    id INTEGER PRIMARY KEY,
    draft_id INTEGER,
    celeb_name TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    url TEXT NOT NULL,
    log_no TEXT,
    fact_sources TEXT,       -- JSON: 분쟁 대비 팩트 출처 스냅샷
    asset_refs TEXT,         -- JSON: 사용 에셋 출처/라이선스 스냅샷
    post_review_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(post_review_status IN ('pending','keep','edit','takedown')),
    FOREIGN KEY (draft_id) REFERENCES drafts(id)
);

CREATE TABLE IF NOT EXISTS instagram_watchlist (
    id INTEGER PRIMARY KEY,
    celeb_name TEXT NOT NULL,
    post_url TEXT NOT NULL,
    added_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

-- YouTube 검색 실패 네거티브 캐시(2026-07-14 신설) — 셀럽명으로 검색해도
-- 매칭 영상이 없던 이력을 24시간 기억해, 같은 날 재시도(후보 폴백/스케줄러
-- 재실행)에서 똑같이 실패할 검색에 쿼터를 또 쓰지 않게 한다.
CREATE TABLE IF NOT EXISTS youtube_search_negative_cache (
    id INTEGER PRIMARY KEY,
    celeb_name TEXT NOT NULL UNIQUE,
    checked_at TEXT NOT NULL
);
"""


@contextmanager
def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """테이블이 없으면 생성한다. 여러 번 호출해도 안전(IF NOT EXISTS)."""
    with _conn() as conn:
        conn.executescript(SCHEMA)


# ── trend_candidates ────────────────────────────────────────────

def add_trend_candidate(celeb_name: str, topic: str, sub_tags: list[str], signal_score: float) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO trend_candidates (celeb_name, topic, sub_tags, signal_score, detected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (celeb_name, topic, json.dumps(sub_tags, ensure_ascii=False), signal_score,
             datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_recent_celeb_topics(days: int = 14) -> set[tuple[str, str]]:
    """최근 N일간 이미 발행한 (celeb_name, sub_tag) 조합 집합 — 반복 방지용."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT celeb_name, asset_refs FROM published WHERE published_at >= ?",
            (cutoff,),
        ).fetchall()
    # published에는 sub_tags를 직접 안 남기므로 draft 조인 대신 celeb_name만 기준으로 최소 방지.
    # (세부 sub_tag 중복은 celeb_watchlist.discover_candidates()의 signal_score 감점으로 보정)
    return {(r["celeb_name"], "") for r in rows}


def get_recent_published_celebs(days: int = 14) -> list[str]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT celeb_name FROM published WHERE published_at >= ?",
            (cutoff,),
        ).fetchall()
    return [r["celeb_name"] for r in rows]


# ── facts ────────────────────────────────────────────────────────

def add_fact(celeb_name: str, sub_tags: list[str], fact_text: str, source_count: int, status: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO facts (celeb_name, sub_tags, fact_text, source_count, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (celeb_name, json.dumps(sub_tags, ensure_ascii=False), fact_text, source_count,
             status, datetime.now().isoformat()),
        )
        return cur.lastrowid


# ── assets ───────────────────────────────────────────────────────

def add_asset(celeb_name: str, type_: str, ref: str, license_note: str = "", alt_text: str = "") -> int:
    if type_ == "download_reupload":
        raise ValueError("download_reupload 자산 타입은 금지되어 있습니다 (원칙 ② 참조)")
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO assets (celeb_name, type, ref, license_note, alt_text) VALUES (?, ?, ?, ?, ?)",
            (celeb_name, type_, ref, license_note, alt_text),
        )
        return cur.lastrowid


# ── drafts ───────────────────────────────────────────────────────

def add_draft(celeb_name: str, category: str, title_candidates: list[str], body_markdown: str,
              hashtags: list[str], asset_placements: dict, status: str = "pending_review",
              block_reason: str = "") -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO drafts (celeb_name, category, title_candidates, body_markdown, hashtags, "
            "asset_placements, status, block_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (celeb_name, category, json.dumps(title_candidates, ensure_ascii=False), body_markdown,
             json.dumps(hashtags, ensure_ascii=False), json.dumps(asset_placements, ensure_ascii=False),
             status, block_reason, datetime.now().isoformat()),
        )
        return cur.lastrowid


def update_draft_status(draft_id: int, status: str, block_reason: str = ""):
    with _conn() as conn:
        conn.execute(
            "UPDATE drafts SET status = ?, block_reason = ? WHERE id = ?",
            (status, block_reason, draft_id),
        )


# ── published ────────────────────────────────────────────────────

def count_published_today() -> int:
    """오늘 일일 발행 상한(CELEB_MAX_DAILY_POSTS) 대비 카운트.

    post_review_status='takedown'인 글은 제외한다 — 2026-07-15 실측 버그: 사후
    검수로 삭제(delete_post + mark_post_review takedown)한 글도 published 테이블에
    행이 남아있어 그대로 카운트되면서, 실제로는 서로 다른 셀럽 2건만 살아있는데
    상한(3)에 도달한 것으로 잘못 계산 — 저녁 슬롯이 "오늘 이미 3/3건 발행됨"으로
    조용히 건너뛰어졌다(리한나 글을 삭제 후 재발행하는 과정에서 발생)."""
    today = date.today().isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM published WHERE published_at LIKE ? "
            "AND post_review_status != 'takedown'",
            (f"{today}%",),
        ).fetchone()
    return row["n"]


def log_published(draft_id: int, celeb_name: str, title: str, url: str, log_no: str,
                   fact_sources: list, asset_refs: list) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO published (draft_id, celeb_name, title, published_at, url, log_no, "
            "fact_sources, asset_refs) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (draft_id, celeb_name, title, datetime.now().isoformat(), url, log_no,
             json.dumps(fact_sources, ensure_ascii=False), json.dumps(asset_refs, ensure_ascii=False)),
        )
        return cur.lastrowid


def list_recent_published(limit: int = 20) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM published ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()


def get_published(id_: int) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM published WHERE id = ?", (id_,)).fetchone()


def get_published_by_celeb(celeb_name: str) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM published WHERE celeb_name = ?", (celeb_name,)
        ).fetchall()


def mark_post_review(id_: int, status: str):
    with _conn() as conn:
        conn.execute("UPDATE published SET post_review_status = ? WHERE id = ?", (status, id_))


# ── instagram_watchlist ─────────────────────────────────────────

def add_instagram_url(celeb_name: str, post_url: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO instagram_watchlist (celeb_name, post_url, added_at, used) VALUES (?, ?, ?, 0)",
            (celeb_name, post_url, datetime.now().isoformat()),
        )
        return cur.lastrowid


def get_unused_instagram_url(celeb_name: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM instagram_watchlist WHERE celeb_name = ? AND used = 0 "
            "ORDER BY added_at ASC LIMIT 1",
            (celeb_name,),
        ).fetchone()


def mark_instagram_url_used(id_: int):
    with _conn() as conn:
        conn.execute("UPDATE instagram_watchlist SET used = 1 WHERE id = ?", (id_,))


# ── youtube_search_negative_cache ───────────────────────────────

def is_youtube_search_negative_cached(celeb_name: str, hours: int = 24) -> bool:
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM youtube_search_negative_cache WHERE celeb_name = ? AND checked_at >= ?",
            (celeb_name, cutoff),
        ).fetchone()
        return row is not None


def add_youtube_search_negative_cache(celeb_name: str):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO youtube_search_negative_cache (celeb_name, checked_at) VALUES (?, ?) "
            "ON CONFLICT(celeb_name) DO UPDATE SET checked_at = excluded.checked_at",
            (celeb_name, datetime.now().isoformat()),
        )


if __name__ == "__main__":
    init_db()
    print(f"DB 초기화 완료: {DB_PATH}")
