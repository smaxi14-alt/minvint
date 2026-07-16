"""발행 전 자동 안전 점검 — 마지막 방어선.

1일 1글 무인 자동화 체제에서는 사람이 매일 이미지·본문을 리뷰하지 않는다.
_context/blog-style-guide.md §6, _context/brand-guidelines.md 금지사항,
blog-writing-program.md PART 5 금지 목록에서 가장 자주 위반되는 항목만
정규식으로 스캔한다 — 완벽한 검수를 대체하지 않는다. 위반 발견 시
run_daily_blog.py는 발행을 중단하고 수동 검토로 넘긴다(자동 폐기·자동 발행 둘 다 아님).
"""
import re

BANNED_PHRASES = [
    "무조건", "반드시", "최고의", "유일한", "꼭 드셔야",
    "고객님의 소중한 보험", "저는 정직한 설계사입니다",
    "하시면 후회합니다", "지금 바로 상담 신청하세요",
    "1등", "완벽한", "보험 추천", "좋은 보험",
]

BANNED_TAG_FRAGMENTS = ["추천", "1위", "최고", "특가", "이벤트"]

# 2026-07-15 실측 발견: "인터넷에 '실손 무조건 갈아타라'는 글이 많은데,
# '무조건'이 붙는 순간 의심하시는 게 맞습니다" 같은, 금지어를 오히려 경계하라고
# 비판·인용하는 문장이 순수 부분 문자열 매칭 때문에 거짓양성으로 발행 보류됨
# (needs_review/2026-07-15.json). 작은따옴표로 인용된 구간 안에서의 언급은
# 글쓴이 본인의 주장이 아니라 인용/비판 대상이므로 위반으로 치지 않는다.
_QUOTE_PATTERN = re.compile(r"'[^']*'")


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _QUOTE_PATTERN.finditer(text)]


def _has_unquoted_occurrence(phrase: str, body: str, quoted_spans: list[tuple[int, int]]) -> bool:
    for m in re.finditer(re.escape(phrase), body):
        if not any(start <= m.start() < end for start, end in quoted_spans):
            return True
    return False


def check_body(body: str) -> list[str]:
    """본문 위반 사항 목록 반환. 빈 리스트면 통과."""
    violations = []
    quoted_spans = _quoted_spans(body)
    for phrase in BANNED_PHRASES:
        if _has_unquoted_occurrence(phrase, body, quoted_spans):
            violations.append(f"금지 표현 검출: '{phrase}'")
    if "!" in body:
        violations.append("느낌표(!) 검출 — blog-style-guide.md §2 문장 종결 규칙 위반")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", body):
        violations.append("이모지/특수기호 검출 — blog-style-guide.md §2 위반")
    return violations


def check_tags(tags: list[str]) -> list[str]:
    """해시태그 컴플라이언스 위반 목록 반환."""
    violations = []
    for t in tags:
        for frag in BANNED_TAG_FRAGMENTS:
            if frag in t:
                violations.append(f"해시태그 컴플라이언스 위험: '#{t}' ('{frag}' 포함)")
    return violations


def check_title(title: str) -> list[str]:
    """제목 전용 위반 목록 반환(2026-07-15 신설). 본문과 동일한 BANNED_PHRASES를
    제목에도 적용한다 — 이전엔 title이 어느 안전검사에도 안 거쳐 그대로
    발행됐다. 본문 검사(check_body)와 분리해 둔 이유는, run_daily_blog.py의
    재시도 로직이 "위반이 제목에만 있다"를 판별해 재생성 없이 title_candidates
    중 다른 후보로 교체(저비용 1차 복구)할 수 있게 하기 위함이다."""
    violations = []
    for phrase in BANNED_PHRASES:
        if phrase in title:
            violations.append(f"제목 금지 표현 검출: '{phrase}'")
    return violations


def run_all_checks(body: str, tags: list[str], title: str = "") -> list[str]:
    """본문+태그(+제목) 전체 위반 목록. 빈 리스트면 발행 진행 가능.
    title은 선택 인자(기본값 "" — 기존 호출부 하위 호환, 검사 생략)."""
    violations = check_body(body) + check_tags(tags)
    if title:
        violations += check_title(title)
    return violations
