"""실시간 시장 데이터 조회 — economy_news 콘텐츠의 수치 신뢰성 보완 장치.

배경: content_generator.py는 "코스피 등락보다 중요한..." 같은 짧은 소재 문구만
LLM에 전달하고, 실제 코스피 지수·삼성전자 주가 같은 구체적 수치는 LLM이 학습
데이터 시점 기준으로 스스로 지어냈다(2026-07-05 "삼성전자 8만원" 발행 사례,
trend_cache.json의 "코스피 7475선" 캐시 소재 등 — 실시간 검증 없이 그대로
글감으로 흘러들어갈 수 있는 구조였음). 이 모듈은 발행 직전 실제 수치를 가져와
프롬프트에 주입하고, 생성된 글의 수치를 사후 대조하는 두 단계 안전장치를 만든다.

데이터 출처: 네이버 금융 공개 폴링 API (인증 불필요, finance.naver.com이 프론트엔드에서
직접 호출하는 엔드포인트 — 2026-07-14 실측 확인). 실패해도 예외를 전파하지 않고
None을 반환한다 — market_data 조회 실패가 전체 발행 파이프라인을 막으면 안 된다.
"""
import json
import logging
import re
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}

_KOSPI_URL = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI"
_SAMSUNG_URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/005930"
_FX_USDKRW_URL = (
    "https://m.stock.naver.com/front-api/marketIndex/prices"
    "?type=exchange&category=exchange&reutersCode=FX_USDKRW"
)

# 검증 대상 종목/지수 — 프롬프트에 주입하는 값과 사후 검증 정규식을 여기서 함께 관리한다.
# 새 종목을 추가하려면 fetch 함수 하나 + 이 dict에 라벨만 추가하면 된다.
INSTRUMENT_LABELS = {
    "kospi": "코스피",
    "samsung": "삼성전자",
    "usdkrw": "원달러 환율",
}


def _fetch_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        logger.warning(f"[market_data] 조회 실패: {url} — {e}")
        return None
    except Exception as e:
        logger.warning(f"[market_data] 예외: {url} — {e}")
        return None


def _get_kospi() -> float | None:
    data = _fetch_json(_KOSPI_URL)
    try:
        return float(data["datas"][0]["closePrice"].replace(",", ""))
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def _get_samsung() -> float | None:
    data = _fetch_json(_SAMSUNG_URL)
    try:
        return float(data["datas"][0]["closePrice"].replace(",", ""))
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def _get_usdkrw() -> float | None:
    data = _fetch_json(_FX_USDKRW_URL)
    try:
        return float(data["result"][0]["closePrice"].replace(",", ""))
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def get_market_snapshot() -> dict[str, float]:
    """조회 가능한 실시간 수치만 담아 반환한다. 전부 실패하면 빈 dict —
    호출부는 빈 dict일 때 "실제 수치 없음, 구체적 숫자 언급 금지" 지침으로 대체한다."""
    snapshot = {}
    for key, fetch_fn in (("kospi", _get_kospi), ("samsung", _get_samsung), ("usdkrw", _get_usdkrw)):
        value = fetch_fn()
        if value is not None:
            snapshot[key] = value
    if snapshot:
        logger.info(f"[market_data] 실시간 스냅샷 확보: {snapshot}")
    else:
        logger.warning("[market_data] 실시간 데이터 전부 조회 실패 — 구체적 수치 언급 금지 지침으로 대체")
    return snapshot


def format_snapshot_for_prompt(snapshot: dict[str, float]) -> str:
    """프롬프트에 그대로 삽입할 문자열 블록을 만든다."""
    if not snapshot:
        return (
            "실시간 시장 데이터 조회에 실패했습니다. 코스피·삼성전자·환율 등 실제 시세를 "
            "구체적 숫자로 언급하지 마세요(추측 금지). 대신 방향성·흐름 위주로만 서술하세요 "
            "(예: \"코스피가 최근 오름세다\" ○ / \"코스피 3200선\" 같은 특정 수치 ✕)."
        )
    lines = ["오늘 기준 실제 시장 데이터 (아래 수치만 사용 가능 — 다른 구체적 시세·지수·주가 숫자는 절대 지어내지 마세요):"]
    if "kospi" in snapshot:
        lines.append(f"- 코스피: {snapshot['kospi']:,.2f}")
    if "samsung" in snapshot:
        lines.append(f"- 삼성전자: {snapshot['samsung']:,.0f}원")
    if "usdkrw" in snapshot:
        lines.append(f"- 원달러 환율: {snapshot['usdkrw']:,.2f}원")
    lines.append(
        "위 목록에 없는 종목·지수(예: 코스닥, 기준금리, 다른 개별 종목)를 언급할 때는 "
        "구체적 숫자 없이 방향성만 서술하세요."
    )
    return "\n".join(lines)


# 생성된 본문에서 "코스피 6,526" / "삼성전자 251,000원" / "삼성전자 8만원" 류
# 표현을 찾아 실제 수치와 대조하기 위한 정규식. 특히 "8만원"·"25만4천원" 같은
# 한국어 만/천 단위 축약 표기는 실제 사고 사례(2026-07-05 "삼성전자 8만원에
# 팔고 나왔다")의 표기 형태라 별도로 반드시 잡아야 한다 — 일반 숫자 정규식만으론
# 놓친다("8"이라는 한 자리 숫자만 매치되고 "만원"은 무시됨).
_PLAIN_NUMBER = r"([0-9][0-9,]{2,})"

_NUMBER_AFTER_LABEL_RE = {
    "kospi": re.compile(rf"코스피[^0-9]{{0,6}}{_PLAIN_NUMBER}"),
    "samsung_plain": re.compile(rf"삼성전자[^0-9]{{0,6}}{_PLAIN_NUMBER}\s*원"),
    "samsung_won_unit": re.compile(r"삼성전자[^0-9만]{0,6}([0-9]+)\s*만\s*([0-9]+)?\s*천?\s*원"),
    "usdkrw": re.compile(rf"(?:원달러|달러\s*환율|환율)[^0-9]{{0,6}}{_PLAIN_NUMBER}\s*원"),
}

# key 접미사(_plain/_won_unit)는 검증 로직 전용 구분자일 뿐, 실제 대조는
# INSTRUMENT_LABELS의 기본 key(kospi/samsung/usdkrw)로 정규화해서 수행한다.
_MATCH_KEY_TO_INSTRUMENT = {
    "kospi": "kospi",
    "samsung_plain": "samsung",
    "samsung_won_unit": "samsung",
    "usdkrw": "usdkrw",
}

# 실제 값과 이 비율 이상 차이나면 "지어낸 숫자"로 간주한다. 코스피·환율은 일중
# 변동이 커도 이 정도 오차 안에 들어오고, 이보다 크게 벗어나면 사실상 다른
# 시점(구식 학습 데이터) 기준 숫자를 썼다는 뜻이다.
_TOLERANCE = 0.05


def _extract_claimed_value(match_key: str, match: re.Match) -> float:
    if match_key == "samsung_won_unit":
        man = int(match.group(1) or 0)
        cheon = int(match.group(2) or 0)
        return man * 10_000 + cheon * 1_000
    return float(match.group(1).replace(",", ""))


def check_market_numbers(text: str, snapshot: dict[str, float]) -> list[str]:
    """생성된 글에서 실제 시세와 동떨어진 구체적 수치를 찾아 위반 사유 목록을 반환한다.
    snapshot이 비어 있으면(조회 실패) 애초에 "숫자 언급 금지"를 프롬프트로 지시했으므로,
    이 경우엔 코스피/삼성전자/환율에 구체적 숫자가 하나라도 등장하면 그 자체가 위반이다."""
    violations = []
    seen_instruments = set()
    for match_key, pattern in _NUMBER_AFTER_LABEL_RE.items():
        key = _MATCH_KEY_TO_INSTRUMENT[match_key]
        if key in seen_instruments:
            continue  # 같은 종목이 plain/won_unit 두 패턴에 동시에 걸려도 한 번만 검증
        match = pattern.search(text)
        if not match:
            continue
        seen_instruments.add(key)
        claimed = _extract_claimed_value(match_key, match)
        actual = snapshot.get(key)
        if actual is None:
            violations.append(
                f"{INSTRUMENT_LABELS[key]} 실시간 데이터 조회 실패 상태였는데 "
                f"본문에 구체적 숫자({claimed:,.0f})가 등장함 — 지어낸 수치일 가능성"
            )
            continue
        diff_ratio = abs(claimed - actual) / actual
        if diff_ratio > _TOLERANCE:
            violations.append(
                f"{INSTRUMENT_LABELS[key]} 본문 언급값 {claimed:,.0f} vs 실제값 {actual:,.2f} "
                f"(오차 {diff_ratio:.1%}, 허용치 {_TOLERANCE:.0%} 초과) — 실시간 수치 미반영 의심"
            )
    return violations


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    snap = get_market_snapshot()
    print(format_snapshot_for_prompt(snap))
    print()
    print("검증 테스트:")
    print(check_market_numbers("삼성전자 8만원에 팔았다. 코스피는 3200선이다.", snap))
