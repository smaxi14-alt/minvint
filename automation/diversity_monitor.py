"""
다양성 모니터 + 자동 보정 스크립트

Usage:
  python diversity_monitor.py            # 1회 분석 + 보정 파일 업데이트
  python diversity_monitor.py --watch    # 지정 간격(기본 1시간) 지속 감시
  python diversity_monitor.py --report   # 보고서만 출력 (보정 파일 수정 안 함)
  python diversity_monitor.py --watch --interval 1800   # 30분 간격
"""

import json
import sys
import time
import logging
import argparse
from datetime import date, datetime, timedelta
from collections import Counter

# Windows 콘솔 UTF-8 강제 (박스 문자 출력)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import LOGS_DIR, DATA_DIR, START_DATE, today_kst

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

LOG_FILE      = LOGS_DIR / "posts_log.json"
MONITOR_LOG   = LOGS_DIR / "diversity_monitor.log"
OVERRIDE_FILE = DATA_DIR / "diversity_override.json"
SEEDS_FILE    = DATA_DIR / "content_seeds.json"

# content_generator.py의 THEME_COOLDOWN_DAYS(하드 컷)와 동일한 값으로 맞춘다 — 두 값이
# 어긋나면 여기서 "정상"이라고 보고해도 실제 생성 단계에서는 다른 기간으로 걸러질 수 있다.
# (import로 공유하지 않는 이유: content_generator.py는 모듈 로드 시 Anthropic 클라이언트를
# 생성해 ANTHROPIC_API_KEY가 없는 실행 환경 — 예: GitHub Actions의 "Refresh diversity
# override" 스텝 — 에서 임포트만으로 깨질 수 있음. 두 파일을 독립적으로 유지.)
THEME_COOLDOWN_DAYS = 60

# ── 임계값 ────────────────────────────────────────────────────
THRESHOLDS = {
    "ending": {
        "window_days": 14,
        "max_ratio": 0.55,
        "targets": {"질문형": 35, "선언형": 25, "독백형": 20, "관찰형": 15, "여운형": 5},
    },
    "template":     {"window_days": 14, "max_ratio": 0.40},
    "tone":         {"window_days": 14, "max_ratio": 0.45},
    # 2026-07-24: 7일 창은 "일주일 내 완전히 같은 소재"만 잡아서, 18일 간격으로 재발행된
    # 반복 사고를 못 잡았다(아이 방 엿듣기 소재 7/6, 7/24 중복 발행). content_generator.py의
    # THEME_COOLDOWN_DAYS와 맞춰 60일로 확장 — 이건 override 파일을 통한 보조 안전장치이고,
    # 주 방어선은 content_generator.py의 하드 컷이다.
    "theme":        {"window_days": THEME_COOLDOWN_DAYS, "max_count": 1},
    "pool_exhaustion": {"warn_ratio": 0.5},  # 쿨다운 창 내 폴 소진 비율이 이 이상이면 경고
    "content_type": {
        "window_days": 14,
        "agro_types": ["agro", "agro_finance"],
        # Phase별 허용 비율: Phase 1 팔로워 확보기엔 agro 위주, 이후 전문가 콘텐츠로 전환
        "max_agro_ratio": {1: 0.75, 2: 0.30, 3: 0.10},
    },
}

# ── 데이터 로딩 ───────────────────────────────────────────────

def _load_posts() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("posts", [])


def _in_window(posts: list[dict], days: int) -> list[dict]:
    cutoff = today_kst() - timedelta(days=days)
    return [p for p in posts if date.fromisoformat(p["date"]) >= cutoff]


# ── 분석 함수 ─────────────────────────────────────────────────

def _analyze_ratio(posts: list[dict], key: str, window_days: int, max_ratio: float) -> dict:
    window = _in_window(posts, window_days)
    counts = Counter(p.get(key, "") for p in window if p.get(key))
    total  = sum(counts.values())
    if total == 0:
        return {"ok": True, "counts": {}, "ratios": {}, "warnings": [], "total": 0}
    ratios   = {k: v / total for k, v in counts.items()}
    warnings = [
        f"[{key}/{k}] {r:.0%} — 임계값 {max_ratio:.0%} 초과"
        for k, r in ratios.items() if r > max_ratio
    ]
    return {"ok": not warnings, "counts": dict(counts), "ratios": ratios,
            "warnings": warnings, "total": total}


def analyze_endings(posts: list[dict]) -> dict:
    cfg = THRESHOLDS["ending"]
    return _analyze_ratio(posts, "ending_style", cfg["window_days"], cfg["max_ratio"])


def analyze_templates(posts: list[dict]) -> dict:
    cfg = THRESHOLDS["template"]
    return _analyze_ratio(posts, "template", cfg["window_days"], cfg["max_ratio"])


def analyze_tones(posts: list[dict]) -> dict:
    cfg = THRESHOLDS["tone"]
    return _analyze_ratio(posts, "tone", cfg["window_days"], cfg["max_ratio"])


def _load_seed_pools() -> dict[str, list[str]]:
    if not SEEDS_FILE.exists():
        return {}
    with open(SEEDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("themes", {})


def analyze_pool_exhaustion(posts: list[dict]) -> dict:
    """시드 폴 크기 대비 최근 소비량을 비교해 폴 소진을 경고한다.

    theme 하드 쿨다운(THEME_COOLDOWN_DAYS)만 믿고 있으면, 폴이 (쿨다운일수 × 하루 소비
    슬롯수)보다 작을 때 결국 폴이 매번 바닥나 다시 반복이 재발한다 — 2026-07-24
    agro_empathy 35개 폴이 Phase1 하루 2슬롯 소비로 18일 만에 고갈된 사례가 실제로 있었다.
    이 경고가 뜨면 코드가 아니라 content_seeds.json에 소재를 더 추가해야 한다."""
    pools = _load_seed_pools()
    if not pools:
        return {"ok": True, "pools": {}, "warnings": []}

    window = _in_window(posts, THEME_COOLDOWN_DAYS)
    used_themes = {p.get("theme", "") for p in window if p.get("theme")}

    warn_ratio = THRESHOLDS["pool_exhaustion"]["warn_ratio"]
    result: dict[str, dict] = {}
    warnings: list[str] = []
    for pool_name, items in pools.items():
        total = len(items)
        if total == 0:
            continue
        used = sum(1 for t in items if t in used_themes)
        ratio = used / total
        result[pool_name] = {"total": total, "used": used, "ratio": ratio}
        if ratio >= warn_ratio:
            warnings.append(
                f"[소재풀 소진] '{pool_name}' 최근 {THEME_COOLDOWN_DAYS}일 내 {used}/{total}개"
                f"({ratio:.0%}) 소진 — content_seeds.json에 소재 추가 필요"
            )
    return {"ok": not warnings, "pools": result, "warnings": warnings}


def analyze_themes(posts: list[dict]) -> dict:
    cfg  = THRESHOLDS["theme"]
    win  = _in_window(posts, cfg["window_days"])
    counts = Counter(p.get("theme", "") for p in win if p.get("theme"))
    repeated = {t: c for t, c in counts.items() if c >= cfg["max_count"]}
    warnings = [
        f"[소재] '{t[:35]}' — {cfg['window_days']}일 내 {c}회 반복"
        for t, c in repeated.items()
    ]
    return {"ok": not warnings, "counts": dict(counts),
            "repeated": repeated, "warnings": warnings}


def _current_phase() -> int:
    days = (today_kst() - START_DATE).days
    if days < 90:
        return 1
    elif days < 180:
        return 2
    return 3


def analyze_content_types(posts: list[dict]) -> dict:
    """일상글(agro/agro_finance) 비율이 Phase별 목표치를 초과하는지 분석."""
    cfg   = THRESHOLDS["content_type"]
    phase = _current_phase()
    max_agro = cfg["max_agro_ratio"][phase]

    win = _in_window(posts, cfg["window_days"])
    counts = Counter(p.get("content_type", "") for p in win if p.get("content_type"))
    total = sum(counts.values())
    if total == 0:
        return {"ok": True, "counts": {}, "ratios": {}, "agro_ratio": 0.0,
                "warnings": [], "total": 0, "phase": phase, "max_agro": max_agro}
    agro_count = sum(counts.get(t, 0) for t in cfg["agro_types"])
    agro_ratio = agro_count / total
    ratios = {k: v / total for k, v in counts.items()}
    warnings = []
    if agro_ratio > max_agro:
        warnings.append(
            f"[content_type] 일상글 비율 {agro_ratio:.0%} — Phase {phase} 목표 {max_agro:.0%} 초과 "
            f"(agro={counts.get('agro',0)} agro_finance={counts.get('agro_finance',0)} / 전체 {total})"
        )
    return {"ok": not warnings, "counts": dict(counts), "ratios": ratios,
            "agro_ratio": agro_ratio, "warnings": warnings, "total": total,
            "phase": phase, "max_agro": max_agro}


# ── 자동 보정 계산 ────────────────────────────────────────────
# ending/template/tone에 대한 boost 계산은 제거됐다(2026-07-21) — content_generator.py의
# _diversity_pick()이 이미 동일한 14일 최근사용 패널티를 자체적으로 매겨서 중복이었고,
# 특히 template_boosts는 content_generator.py 어디서도 읽지 않는 죽은 값이었다.
# 여기 남기는 건 content_generator.py가 자체적으로 처리하지 못하는 두 가지뿐:
# theme_exclusions(7일 하드컷 — 자체 로직은 14일 소프트 패널티뿐이라 더 강한 컷이 필요)와
# content_type_exclusions(Phase별 일상글 비율 강제 — 이건 diversity_monitor만 아는 규칙).

def build_override(theme_r, content_type_r) -> dict:
    # 일상글 비율이 Phase별 목표를 초과할 때만 agro 계열 제외
    ct_cfg   = THRESHOLDS["content_type"]
    max_agro = content_type_r.get("max_agro", ct_cfg["max_agro_ratio"].get(_current_phase(), 0.10))
    ct_excl  = (
        list(ct_cfg["agro_types"])
        if content_type_r["agro_ratio"] > max_agro
        else []
    )
    return {
        "generated_at":            datetime.now().isoformat(timespec="seconds"),
        "theme_exclusions":        list(theme_r.get("repeated", {}).keys()),
        "content_type_exclusions": ct_excl,
    }


def save_override(override: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(OVERRIDE_FILE, "w", encoding="utf-8") as f:
        json.dump(override, f, ensure_ascii=False, indent=2)


# ── 보고서 출력 ───────────────────────────────────────────────

def _bar(ratio: float, width: int = 18) -> str:
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled)


def print_report(ending_r, template_r, tone_r, theme_r, content_type_r, override, pool_r=None):
    W = 54
    print(f"\n{'═'*W}")
    print(f"  다양성 모니터 보고서  {today_kst().isoformat()}")
    print(f"{'═'*W}")

    # 콘텐츠 타입 비율 (최상단 표시)
    ct_total = content_type_r["total"]
    ct_phase = content_type_r.get("phase", _current_phase())
    ct_max   = content_type_r.get("max_agro", 0.10)
    print(f"\n[콘텐츠 타입 비율] (최근 {ct_total}개 / {THRESHOLDS['content_type']['window_days']}일) ← Phase {ct_phase} 일상글 목표 {ct_max:.0%} 이하")
    print("─" * W)
    agro_types = set(THRESHOLDS["content_type"]["agro_types"])
    for kind, cnt in sorted(content_type_r["counts"].items(), key=lambda x: -x[1]):
        ratio = content_type_r["ratios"].get(kind, 0)
        tag   = " [일상글]" if kind in agro_types else ""
        flag  = " ⚠" if content_type_r["warnings"] and kind in agro_types and content_type_r["agro_ratio"] > content_type_r.get("max_agro", 0.75) else ""
        print(f"  {kind:<18} {_bar(ratio)} {ratio:5.1%}  ({cnt}회){tag}{flag}")
    if not content_type_r["counts"]:
        print("  데이터 없음")

    for label, result in [("엔딩 스타일", ending_r), ("템플릿", template_r), ("어투", tone_r)]:
        total = result["total"]
        print(f"\n[{label}] (최근 {total}개 / {THRESHOLDS.get(label[:2], {}).get('window_days', 14)}일)")
        print("─" * W)
        for kind, cnt in sorted(result["counts"].items(), key=lambda x: -x[1]):
            ratio = result["ratios"].get(kind, 0)
            flag  = " ⚠" if result["warnings"] and any(kind in w for w in result["warnings"]) else ""
            print(f"  {kind:<8} {_bar(ratio)} {ratio:5.1%}  ({cnt}회){flag}")

    print(f"\n[소재 반복] (최근 {THRESHOLDS['theme']['window_days']}일)")
    print("─" * W)
    if theme_r["repeated"]:
        for theme, cnt in theme_r["repeated"].items():
            print(f"  ⚠  {theme[:40]} — {cnt}회")
    else:
        print("  없음 (정상)")

    pool_r = pool_r or {"pools": {}, "warnings": []}
    print(f"\n[소재풀 소진율] (최근 {THEME_COOLDOWN_DAYS}일 하드 쿨다운 기준)")
    print("─" * W)
    if pool_r["pools"]:
        for name, p in sorted(pool_r["pools"].items(), key=lambda x: -x[1]["ratio"]):
            flag = " ⚠" if p["ratio"] >= THRESHOLDS["pool_exhaustion"]["warn_ratio"] else ""
            print(f"  {name:<18} {_bar(p['ratio'])} {p['used']:>3}/{p['total']:<3}({p['ratio']:5.0%}){flag}")
    else:
        print("  content_seeds.json 없음 — 확인 불가")

    all_w = (content_type_r["warnings"] + ending_r["warnings"] +
             template_r["warnings"] + tone_r["warnings"] + theme_r["warnings"] +
             pool_r["warnings"])
    print(f"\n[경고] {len(all_w)}건")
    print("─" * W)
    for w in all_w:
        print(f"  ⚠  {w}")
    if not all_w:
        print("  없음 — 다양성 정상")

    print(f"\n[자동 보정 → {OVERRIDE_FILE.name}]")
    print("─" * W)
    has_fix = False
    if override.get("content_type_exclusions"):
        print(f"  콘텐츠타입 제외: {override['content_type_exclusions']}  ← 일상글 비율 초과")
        has_fix = True
    if override["theme_exclusions"]:
        print(f"  소재 제외    : {[t[:28] for t in override['theme_exclusions']]}")
        has_fix = True
    if not has_fix:
        print("  보정 불필요 — 균형 정상")
    print(f"{'═'*W}\n")


def _append_monitor_log(warnings: list[str]):
    LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    with open(MONITOR_LOG, "a", encoding="utf-8") as f:
        if warnings:
            for w in warnings:
                f.write(f"{ts} WARN  {w}\n")
        else:
            f.write(f"{ts} INFO  다양성 정상\n")


# ── 메인 실행 ─────────────────────────────────────────────────

def run_once(dry_run: bool = False, silent: bool = False) -> bool:
    """1회 분석 + 보정 파일 업데이트.

    silent=True 이면 보고서 출력 없이 override 파일만 갱신 (스케줄러 내부 호출용).
    """
    posts = _load_posts()
    if not posts:
        logger.info("포스트 로그 없음.")
        return False

    ending_r      = analyze_endings(posts)
    template_r    = analyze_templates(posts)
    tone_r        = analyze_tones(posts)
    theme_r       = analyze_themes(posts)
    content_type_r = analyze_content_types(posts)
    pool_r        = analyze_pool_exhaustion(posts)
    override      = build_override(theme_r, content_type_r)

    if not silent:
        print_report(ending_r, template_r, tone_r, theme_r, content_type_r, override, pool_r)

    all_warnings = (
        content_type_r["warnings"] + ending_r["warnings"] +
        template_r["warnings"] + tone_r["warnings"] + theme_r["warnings"] +
        pool_r["warnings"]
    )
    _append_monitor_log(all_warnings)

    if not dry_run:
        save_override(override)
        if not silent:
            status = "업데이트" if any([
                override.get("content_type_exclusions"),
                override["theme_exclusions"],
            ]) else "초기화(보정 불필요)"
            logger.info(f"diversity_override.json {status}")
        elif all_warnings:
            logger.info(f"[다양성] 이슈 {len(all_warnings)}건 감지 → override 업데이트")

    return bool(all_warnings)


def main():
    parser = argparse.ArgumentParser(description="쓰레드 다양성 모니터")
    parser.add_argument("--watch",    action="store_true", help="지속 감시 모드")
    parser.add_argument("--report",   action="store_true", help="보고서만 (파일 수정 없음)")
    parser.add_argument("--interval", type=int, default=3600, help="감시 간격(초), 기본 3600")
    args = parser.parse_args()

    if args.watch:
        logger.info(f"감시 모드 시작 — {args.interval}초 간격")
        while True:
            has_issues = run_once(dry_run=args.report)
            if has_issues:
                logger.warning("다양성 이슈 감지 → 보정 파일 업데이트 완료")
            else:
                logger.info("다양성 정상")
            time.sleep(args.interval)
    else:
        run_once(dry_run=args.report)


if __name__ == "__main__":
    main()
