"""
페르소나 일관성 관리 모듈

역할:
  1. get_persona_summary()     — 생성 프롬프트에 삽입할 '확립된 사실' 요약문 반환
  2. update_facts_from_post()  — 발행 완료된 글에서 Claude(haiku)로 새 사실 추출 → 저장소 업데이트
  3. check_contradiction()     — (선택) 생성된 글과 기존 사실 충돌 여부 간이 체크
"""

import json
import logging
from datetime import date
from pathlib import Path

from config import ANTHROPIC_API_KEY, DATA_DIR
from anthropic import Anthropic

logger = logging.getLogger(__name__)

STORE_FILE = DATA_DIR / "persona_store.json"
client = Anthropic(api_key=ANTHROPIC_API_KEY)


# ─────────────────────────────────────────────
# 저장소 I/O
# ─────────────────────────────────────────────

def _load() -> dict:
    if STORE_FILE.exists():
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"facts": {}, "금지_모순": [], "미확정_주의": []}


def _save(store: dict):
    store["last_updated"] = date.today().isoformat()
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# 1. 프롬프트 삽입용 요약
# ─────────────────────────────────────────────

def get_persona_summary() -> str:
    """
    확립된 페르소나 사실을 프롬프트 삽입용 텍스트로 반환.
    글 생성 시 Claude가 이 사실과 모순되지 않도록 안내한다.
    """
    store = _load()
    facts = store.get("facts", {})
    forbidden = store.get("금지_모순", [])
    caution = store.get("미확정_주의", [])

    lines = ["[확립된 페르소나 사실 — 반드시 일관되게 유지]"]

    for category, content in facts.items():
        lines.append(f"\n▸ {category}")
        if isinstance(content, dict):
            for k, v in content.items():
                lines.append(f"  • {k}: {v}")
        elif isinstance(content, list):
            for item in content:
                lines.append(f"  • {item}")
        else:
            lines.append(f"  • {content}")

    if forbidden:
        lines.append("\n[절대 모순 금지]")
        for item in forbidden:
            lines.append(f"  ✕ {item}")

    if caution:
        lines.append("\n[미확정 — 언급 주의]")
        for item in caution:
            lines.append(f"  △ {item}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# 2. 발행 후 사실 자동 추출·저장
# ─────────────────────────────────────────────

def update_facts_from_post(text: str):
    """
    발행 완료된 글에서 Claude haiku로 새로운 페르소나 사실을 추출하고
    persona_store.json에 반영한다.
    기존 사실과 충돌하면 로그로만 경고 (자동 덮어쓰기 안 함).
    """
    store = _load()
    existing_summary = get_persona_summary()

    prompt = f"""아래는 SNS에 발행된 1인칭 글입니다.
글쓴이는 40대 중반 보험설계사(관찰자 포지션)입니다.

이 글에서 **글쓴이 본인**에 대해 새롭게 확인된 사실(가족, 취미, 건강, 재정, 경험 등)이 있으면
JSON 형식으로만 반환하세요.

반환 규칙:
- 새로운 사실이 없으면 빈 객체 {{}} 반환
- 카테고리는 기존 분류 참고: 가족, 취미_경험, 직업_경력, 건강, 재정_자산, 인간관계
- 값은 간결한 한 줄 문장
- JSON 외 텍스트 절대 포함 금지

[기존 확립 사실 참고]
{existing_summary}

[분석할 글]
{text}

JSON 반환:"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # JSON 블록 추출
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()

        new_facts: dict = json.loads(raw)

        if not new_facts:
            logger.info("[persona] 새 사실 없음 — 저장소 변경 없음")
            return

        updated = False
        facts = store.setdefault("facts", {})

        for category, value in new_facts.items():
            if category not in facts:
                facts[category] = value
                logger.info(f"[persona] 새 카테고리 추가: {category} = {value}")
                updated = True
            elif isinstance(facts[category], dict) and isinstance(value, dict):
                for k, v in value.items():
                    if k not in facts[category]:
                        facts[category][k] = v
                        logger.info(f"[persona] 새 사실 추가: {category}.{k} = {v}")
                        updated = True
                    else:
                        # 충돌 감지: 값이 다르면 경고만, 자동 덮어쓰기 안 함
                        if facts[category][k] != v:
                            logger.warning(
                                f"[persona] ⚠️ 충돌 감지 — {category}.{k}\n"
                                f"  기존: {facts[category][k]}\n"
                                f"  신규: {v}\n"
                                f"  → 자동 업데이트 보류. persona_store.json 직접 확인 필요."
                            )
            else:
                if facts.get(category) != value:
                    logger.warning(
                        f"[persona] ⚠️ 충돌 감지 — {category}\n"
                        f"  기존: {facts.get(category)}\n"
                        f"  신규: {value}\n"
                        f"  → 자동 업데이트 보류."
                    )

        if updated:
            _save(store)
            logger.info("[persona] persona_store.json 업데이트 완료")

    except json.JSONDecodeError as e:
        logger.warning(f"[persona] JSON 파싱 실패 ({e}). 원본: {raw[:200]}")
    except Exception as e:
        logger.warning(f"[persona] 사실 추출 실패: {e}")


# ─────────────────────────────────────────────
# 3. 간이 모순 체크 (생성 직후 호출 가능)
# ─────────────────────────────────────────────

def check_contradiction(text: str) -> tuple[bool, str]:
    """
    생성된 글이 기존 확립 사실과 모순되는지 Claude haiku로 검사.
    반환: (모순_있음: bool, 이유: str)
    모순이 없으면 (False, "")
    """
    store = _load()
    forbidden = store.get("금지_모순", [])
    if not forbidden:
        return False, ""

    forbidden_str = "\n".join(f"- {f}" for f in forbidden)
    summary = get_persona_summary()

    prompt = f"""다음 글이 아래 '확립된 사실'과 모순되는지 판단하세요.

[확립된 사실 요약]
{summary}

[판단할 글]
{text}

모순이 있으면: CONFLICT: (이유 한 줄)
모순이 없으면: OK

한 줄만 반환하세요."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip()
        if result.upper().startswith("CONFLICT"):
            reason = result.split(":", 1)[-1].strip()
            logger.warning(f"[persona] 모순 감지: {reason}")
            return True, reason
        return False, ""
    except Exception as e:
        logger.warning(f"[persona] 모순 체크 실패 (무시): {e}")
        return False, ""
