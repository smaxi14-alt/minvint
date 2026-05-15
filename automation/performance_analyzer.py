"""게시물 성과 패턴 분석 — 상위 조합 추출 후 프롬프트에 주입"""
import json
import logging
from datetime import date, timedelta
from config import LOGS_DIR

logger = logging.getLogger(__name__)
LOG_FILE = LOGS_DIR / "posts_log.json"

DIMS = ["content_type", "tone", "ending_style", "template"]


def _score(metrics: dict) -> float:
    """engagement score: likes*3 + replies*2 + reposts*2 + quotes*1 + views*0.01"""
    return (
        metrics.get("likes", 0) * 3
        + metrics.get("replies", 0) * 2
        + metrics.get("reposts", 0) * 2
        + metrics.get("quotes", 0)
        + metrics.get("views", 0) * 0.01
    )


def get_top_patterns(days: int = 30) -> dict:
    """
    최근 N일 데이터에서 차원별 평균 점수 + 상위 콤보 반환.
    지표 데이터가 3개 미만이면 빈 dict 반환.
    """
    if not LOG_FILE.exists():
        return {}

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    posts = [
        p for p in data.get("posts", [])
        if p.get("date", "") >= cutoff and p.get("metrics")
    ]

    if len(posts) < 3:
        return {}

    totals = {d: {} for d in DIMS}
    counts = {d: {} for d in DIMS}

    for p in posts:
        s = _score(p.get("metrics", {}))
        for dim in DIMS:
            val = p.get(dim)
            if not val:
                continue
            totals[dim][val] = totals[dim].get(val, 0) + s
            counts[dim][val] = counts[dim].get(val, 0) + 1

    # 2개 이상 데이터 있는 값만 평균 계산
    avg = {}
    for dim in DIMS:
        avg[dim] = {
            k: totals[dim][k] / counts[dim][k]
            for k in totals[dim]
            if counts[dim][k] >= 2
        }

    # 차원별 1위 추출
    top = {
        dim: max(avg[dim], key=avg[dim].get) if avg[dim] else None
        for dim in DIMS
    }

    return {
        "avg": avg,
        "top": top,
        "total_analyzed": len(posts),
    }


def build_weight_boosts(patterns: dict) -> dict:
    """
    상위 패턴을 기반으로 tone / ending_style 가중치 보정값 반환.
    winner는 가중치 1.5배 부여.
    반환 예: {"tone": "건조한", "ending_style": "선언형"}
    """
    if not patterns:
        return {}
    return {
        dim: patterns["top"].get(dim)
        for dim in ["tone", "ending_style"]
        if patterns["top"].get(dim)
    }


def format_for_prompt(patterns: dict) -> str:
    """Claude 프롬프트에 삽입할 성과 요약 텍스트"""
    if not patterns:
        return "없음 (데이터 축적 중)"

    lines = [f"최근 {patterns['total_analyzed']}개 게시물 분석 결과:"]
    labels = {
        "content_type": "콘텐츠유형",
        "tone": "어투",
        "ending_style": "마무리",
        "template": "템플릿",
    }
    for dim, label in labels.items():
        winner = patterns["top"].get(dim)
        if winner and patterns["avg"].get(dim, {}).get(winner):
            score = patterns["avg"][dim][winner]
            lines.append(f"  {label} 1위: {winner} (평균 점수 {score:.1f})")

    return "\n".join(lines)
