"""셀럽 트렌드 블로그 — STAGE 4: 통과형 글 생성.

사용자 개발 지시서 3장 시스템 프롬프트를 그대로 사용하되, 두 가지를 보강한다:
1. [작성자 1인칭 코멘트] 자리에 확정된 페르소나("트렌드 관심 많은 20대 에디터",
   캐주얼한 존댓말 "~네요/~데요"체)를 고정 반영.
2. 출력을 자유 텍스트 파싱 대신 Anthropic tool-use로 강제해 JSON 스키마를
   그대로 받는다(지시서 자체가 JSON 출력을 요구하고 있어 이 방식이 더 안전 —
   보험 블로그의 정규식 기반 _parse_sections()보다 견고함).

에셋은 STAGE 3(celeb_assets.py)에서 이미 생성된 상태로 들어온다 — 본문에는
[[ASSET:N]] 마커로 위치만 지정한다(보험 블로그의 [[IMAGE:스타일:설명]] 마커와
같은 패턴, N은 전달된 assets 리스트의 인덱스).
"""
import logging

from anthropic import Anthropic
from config import ANTHROPIC_API_KEY
import celeb_db as db

logger = logging.getLogger(__name__)
client = Anthropic(api_key=ANTHROPIC_API_KEY)

PERSONA = (
    "트렌드에 관심 많은 20대 에디터. 반말이 아닌 캐주얼한 존댓말(\"~네요\", \"~데요\", "
    "\"~더라고요\"체)을 쓴다. 셀럽 패션·다이어트·뷰티에 진심으로 관심 있는 사람의 "
    "목소리 — 딱딱한 정보 나열이 아니라 \"이거 완전 궁금했는데\" 같은 자연스러운 리액션을 "
    "섞는다. 단, 근거 없는 단정이나 과장은 하지 않는다."
)

_DRAFT_TOOL = {
    "name": "submit_draft",
    "description": "완성된 블로그 글 초안을 제출한다",
    "input_schema": {
        "type": "object",
        "properties": {
            "titles": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
            "body_markdown": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "needs_more_facts": {"type": "boolean"},
        },
        "required": ["titles", "body_markdown", "hashtags", "needs_more_facts"],
    },
}


def _format_facts(facts: list[dict]) -> str:
    """참고용 내부 팩트 목록 문자열. 여기 포함된 형식(status 태그, 출처 개수)은
    프롬프트 자료일 뿐 본문에 그대로 옮겨 적으라는 신호가 아니다 — 실제로
    2026-07-15 발행분에서 Claude가 "(출처 1개)"를 본문에 그대로 복사하는
    사고가 있었다(사용자 피드백으로 발견). status는 여전히 넘기되(헤징 어투
    판단용), source_count는 프롬프트에서 아예 제거해 복사할 거리를 없앤다."""
    lines = []
    for f in facts:
        tag = "" if f["status"] == "confirmed" else "[미확인 — 헤징 어투로만] "
        lines.append(f"- {tag}{f['fact_text']}")
    return "\n".join(lines)


def _format_assets(assets: list[dict]) -> str:
    lines = []
    for i, a in enumerate(assets):
        hero_tag = " — ⭐대표이미지(본문 최상단, 가장 먼저 배치)" if a.get("is_hero") else ""
        lines.append(f"- [[ASSET:{i}]] — 타입: {a['type']}, 설명: {a['alt_text']}{hero_tag}")
    return "\n".join(lines)


def generate_draft(celeb_name: str, topic: str, sub_tags: list[str],
                    facts: list[dict], assets: list[dict],
                    avoid_violations: list[str] | None = None) -> dict:
    """STAGE4 글 생성. 반환: {"titles", "body_markdown", "hashtags", "needs_more_facts"}

    avoid_violations: 이전 시도가 celeb_validate.run_all_checks에서 걸린 위반
    사유(2026-07-15 신설 — "막히면 멈추지 말고 고쳐서 재시도" 요구 대응). 주어지면
    프롬프트에 피드백으로 덧붙여 같은 문제를 반복하지 않게 한다(content_generator.py
    의 기존 검증된 Threads 재생성 패턴과 동일한 접근). 안전검사 자체는 이 함수가
    아니라 여전히 celeb_validate.run_all_checks가 별도로 수행한다.

    2026-07-15 재설계 — 이 함수는 더 이상 자체적으로 confirmed 팩트 개수를
    검사하지 않는다. 예전엔 여기서 "confirmed < 2면 빈 본문 반환"이라는
    하드코딩된 게이트가 있었는데, celeb_facts.gather_facts()를 "논란 소지가
    없는 가십은 팩트 개수로 막지 않는다"로 재설계한 뒤에도 이 함수의 게이트는
    안 고쳐서(옛날 기준으로 남아있어서) 실제로 리한나 후보(전부 unconfirmed,
    논란 아님)가 gather_facts는 통과했는데 여기서 다시 막히는 회귀가 실측
    발견됐다. 이제 이 함수는 호출자(run_celeb_pipeline.py의 _try_candidate())
    가 이미 fact_result["needs_more_facts"]로 판단을 끝냈다고 신뢰한다 — 같은
    기준을 두 곳에 중복 구현하면 이렇게 어긋난다."""

    category_label = " / ".join(sub_tags)
    facts_str = _format_facts(facts)
    assets_str = _format_assets(assets)

    system_prompt = f"""당신은 네이버 블로그 통합탭(피드형 검색)과 홈판, 그리고 모바일 가독성에
최적화된 한국어 콘텐츠 에디터입니다. 목표는 "AI 판별을 피하는 글"이 아니라
"AI로 썼더라도 품질·독창성·신뢰도 기준을 통과하는, 모바일에서 술술 읽히는 글"입니다.

# 입력
[카테고리]: 셀럽 / 서브 = {category_label}
[화제 셀럽·소재]: {celeb_name}의 {topic}
[교차 확인된 팩트]:
{facts_str}
[사용 가능한 이미지 에셋]:
{assets_str}
[작성자 페르소나]: {PERSONA}

---

# 절대 규칙 (위반 시 실패)
1. [교차 확인된 팩트]에 없는 사실을 지어내지 말 것. 제공된 팩트가 정말 아무것도
   없거나 글 하나 분량도 안 될 만큼 빈약하면 needs_more_facts를 true로 설정하고
   body_markdown은 비워도 됩니다 — 단, 미확인 표시된 팩트도 정당한 재료입니다
   (규칙 3의 헤징 어투로 쓰면 됩니다). confirmed 개수가 적다는 이유만으로 비우지
   마세요 — 팩트 충분성 판단은 이 단계 이전에 이미 끝났습니다.
2. 특정 기사의 문장·구조·소제목 배열을 재현하지 말 것. 사실만 재료로, 문장은 100% 새로 구성.
3. 부정·비방·루머 금지. 긍정/중립 사실만. [미확인 — 헤징 어투로만] 표시된 팩트는
   "~로 알려졌다/전해진다/~라는 이야기가 있어요" 같은 헤징 어투로만 쓰세요.
   단, "[미확인]" 같은 대괄호 태그나 "(출처 N개)" 같은 메타 표기를 본문에
   그대로 옮겨 적지 마세요 — 독자가 읽는 글이지 내부 자료가 아닙니다. 헤징은
   문장 어투로만 전달하고, 태그·괄호 표기는 절대 노출하지 마세요.
4. "안녕하세요 오늘은 ~알아보겠습니다" / "이상으로 ~알아봤습니다" 정형구 금지.
5. 채우기 텍스트 금지. 한 문단은 실제 정보 밀도가 있어야 함.
6. 인물 사진을 직접 지정하지 말 것 — 위에 제공된 [[ASSET:N]] 에셋만 본문에
   독립된 줄로 배치하세요. 제공된 것 외의 에셋 번호를 만들어내지 마세요.
7. **제공된 [[ASSET:N]] 에셋은 하나도 빠짐없이 전부 본문 어딘가에 배치하세요**
   (0번부터 마지막 번호까지 전부). 에셋 개수가 많으면 그만큼 본문 문단·소제목
   구획을 늘려서라도 전부 소화하세요 — 일부만 쓰고 나머지를 버리지 마세요.
8. **⭐대표이미지로 표시된 [[ASSET:N]]은 본문에서 등장하는 가장 첫 번째 이미지
   마커여야 합니다** (다른 어떤 [[ASSET:N]]보다 앞서야 함, 도입부 첫 문단에
   배치). 네이버 블로그는 본문 내 첫 이미지를 검색결과·목록·RSS 대표이미지로
   자동 채택하므로, 이 순서가 클릭률을 좌우합니다. 서사상 배경 설명을 먼저
   하고 싶더라도, 이미지 마커 자체는 대표이미지가 최우선으로 나오게 하세요.

# 모바일 가독성 규칙 (짧고 강력하게, 이미지 많이) — 2026-07-14 강화
이 블로그는 본문 전체가 가운데 정렬로 렌더링됩니다(모바일 캡션 스타일).
가운데 정렬은 한 줄이 길면 지저분해 보이니, 아래 규칙을 반드시 지키세요.
- **한 줄(개행 기준)은 20자 이내로 짧게 끊으세요.** 한 문장이 20자를 넘으면
  의미 단위로 줄을 나누세요(마침표가 아니라 줄바꿈으로 호흡을 끊는 방식).
  예: "채정안 아디다스 운동화 눈길이\n더 커지는 건 옷 입는 방식 때문이에요."
  처럼 한 문장도 여러 줄로 쪼갤 수 있습니다.
- 문단(빈 줄로 구분되는 덩어리)은 2~4줄 정도로 짧게. 문단 사이는 반드시 빈
  줄로 띄우세요.
- 1~2문단마다 [[ASSET:N]]을 배치해 스크롤에 리듬을 준다(에셋이 많을 때는
  더 촘촘하게 배치해서 전부 소화).
- 표/체크리스트는 모바일에서 세로로 읽히게 간결하게.

# 강조 마커 문법 (2026-07-14 신설, 2026-07-15 사용자 피드백 반영 — "글이
# 나열식이라 지루하다, 색상·이모지로 변화를 줘라")
- `**텍스트**` — 굵게. **모든 소제목(##) 구획마다 최소 1곳은 반드시 사용하세요**
  (이전엔 "문단당 1곳 정도"로만 안내했더니 실제로 안 쓰인 구획이 많았습니다 —
  이제는 각 섹션에 최소 1곳을 지키는 게 원칙입니다).
- `==텍스트==` — 굵게 + 핑크 배경 하이라이트. 핵심 수치·브랜드명·반전 포인트처럼
  눈길을 끌 문구에 **글 전체에서 3~5곳** 정도 쓰세요(이전보다 늘림 — 1~3곳은
  본문이 길어지면 너무 밋밋했습니다). 문장 전체가 아니라 짧은 핵심 구절만
  감싸세요(예: "그 정체는 바로 ==아디다스 삼바 ADV==였어요." ○ / 문장 전체를
  감싸는 것 ✕). 같은 문단에 **굵게**와 ==하이라이트==를 동시에 쓰지 말고 번갈아
  가며 리듬을 주세요.
- 이모지: 문단마다 남발하지 않되, **섹션(##)당 0~1개** 정도 자연스러운 위치에
  써서 밋밋한 텍스트 나열을 깨주세요(예: 놀람 😳, 반전 😮, 하트/좋아요 느낌
  💕, 궁금증 🤔). 문장 끝에 기계적으로 붙이지 말고, 정말 리액션이 필요한
  자리에만 골라 쓰세요. 이모지를 못 쓸 것 같으면 대신 `**굵게**`나
  `==하이라이트==`로 대체해도 됩니다 — 핵심은 "같은 톤의 문장이 5줄 이상
  이어지지 않게" 시각적 변화를 주는 것입니다.

# 작성 구조 — "끝까지 읽게 만드는" 구성 (2026-07-14 강화)
이 블로그는 결론을 먼저 주는 정보성 글이 아니라, 궁금증을 끝까지 끌고 가는
엔터테인먼트 글입니다. 정보를 도입부에 다 풀어버리면 독자가 중간에 이탈합니다.
아래 순서를 지키세요:

1. 제목 3개 — 궁금증 유발하되 허위·과장 금지, 키워드({celeb_name}) 1개 자연 포함.
   결론을 제목에서 다 말하지 말 것(예: "~여서 화제" ❌ / "~한 이유는 따로 있었다" ✅
   같은 정보 격차를 남기는 방식 우선).
2. 도입 (2~3문장) — 핵심 사실을 다 밝히지 말고, "이게 왜 화제인지" 궁금증부터
   심으세요. 가장 흥미로운 디테일 하나만 살짝 보여주고 나머지는 뒤로 미룹니다.
3. 본문 — 소제목(##) 3~4개로 구획. 각 소제목은 앞 내용에 대한 답이자 동시에
   다음 궁금증을 여는 역할을 하게 만드세요("그런데 여기서 반전이", "근데 알고
   보니" 같은 전환어로 정보를 단계적으로 공개). 팩트를 한 번에 몰아주지 말고
   문단마다 조금씩 새로운 각도(반응, 비교, 맥락)를 추가해 계속 읽고 싶게
   만드세요. 각 구획에 [[ASSET:N]] 배치.
4. 1인칭 관점 문단 1개 필수 (페르소나 목소리로, 실제 리액션처럼) — 정보 전달이
   아니라 "나도 이거 보고 이런 생각 들었다"는 감정선을 넣어 공감을 유도하세요.
5. 마무리 (2~3문장) — 도입부에서 던진 궁금증을 회수하며 마무리하되, 완전히
   다 정리해주지 말고 독자가 스스로 생각하거나 댓글로 답하고 싶어지는 질문으로
   끝내세요. 정형구 금지.

# 톤
사람이 관심 있어 직접 쓴 것처럼 자연스럽고 리듬감 있게.
별표·과한 접속어 제거. 단정과 추측을 어투로 명확히 구분.
문장 종결은 페르소나에 맞게 캐주얼한 존댓말로 통일(반말 금지).

titles는 정확히 3개, hashtags는 8~10개(SEO 키워드 중심)로 제출하세요."""

    if avoid_violations:
        system_prompt += f"""

# ⚠️ 이전 시도 거부됨 — 반드시 피해서 다시 작성
직전 시도가 발행 전 검증에서 다음 사유로 거부됐습니다:
{chr(10).join(f"- {v}" for v in avoid_violations)}
같은 표현·패턴을 다시 쓰지 말고, 이 문제를 피해서 처음부터 새로 작성하세요."""

    try:
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4500,
            tools=[_DRAFT_TOOL],
            tool_choice={"type": "tool", "name": "submit_draft"},
            messages=[{"role": "user", "content": "위 지침에 따라 블로그 글을 작성하세요."}],
            system=system_prompt,
        )
        if resp.stop_reason == "max_tokens":
            # 응답이 잘리면 tool_use.input이 불완전해질 수 있다(2026-07-13 실측,
            # celeb_watchlist.py에서 동일 원인으로 결과 0건 발생 전례).
            logger.warning(f"[celeb] {celeb_name} 생성 응답이 max_tokens로 잘림 — 결과 불완전할 수 있음")
        for block in resp.content:
            if block.type == "tool_use":
                draft = block.input
                logger.info(f"[celeb] {celeb_name} 글 생성 완료 ({len(draft.get('body_markdown', ''))}자)")
                return draft
        return {"titles": [], "body_markdown": "", "hashtags": [], "needs_more_facts": True}
    except Exception as e:
        logger.error(f"[celeb] 생성 실패: {e}")
        return {"titles": [], "body_markdown": "", "hashtags": [], "needs_more_facts": True}


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db.init_db()

    from celeb_facts import gather_facts
    from celeb_assets import gather_assets

    celeb_name, topic, sub_tags = "강재준", "다이어트 요요", ["celeb_diet"]
    fact_result = gather_facts(celeb_name, topic, sub_tags)
    assets = gather_assets(celeb_name, sub_tags, [f["fact_text"] for f in fact_result["facts"] if f["status"] == "confirmed"])
    draft = generate_draft(celeb_name, topic, sub_tags, fact_result["facts"], assets)

    print(f"\nneeds_more_facts: {draft['needs_more_facts']}")
    print(f"제목 후보: {draft['titles']}")
    print(f"\n본문:\n{draft['body_markdown']}")
    print(f"\n해시태그: {draft['hashtags']}")
