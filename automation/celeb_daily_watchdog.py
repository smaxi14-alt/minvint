"""셀럽 트렌드 블로그 — 발행 감시(watchdog).

2026-07-16 신설 — CelebBlog-Morning(10:00) 스케줄 작업이 STATUS_CONTROL_C_EXIT로
조용히 중단되는 사고가 발생함(원인 미확정 — 보험 블로그 watchdog 조사 때와
같은 OneDrive 동기화 경합 추정). 보험 블로그(1일 1글)와 달리 셀럽 블로그는
하루 최대 CELEB_MAX_DAILY_POSTS건(10/14/19시 슬롯)까지 발행하므로 "오늘
발행됐는가"가 아니라 "지금 시각 기준 있어야 할 건수만큼 있는가"로 판단해야
한다 — 그래야 이미 1~2건 정상 발행된 날에 감시가 또 새 candidate를 찾아
초과 발행하는 사고를 막는다.

CELEB_POST_HOURS 슬롯 시각 이후 일정 시간 지난 지점마다 확인해(스케줄 등록은
setup_celeb_watchdog_scheduler.ps1), 그 시각까지 있어야 할 발행 건수보다
실제 건수가 적으면 run_celeb_pipeline.py를 재실행한다. 재실행 자체는
run_celeb_pipeline.py의 기존 일일 상한 가드(count_published_today() >=
CELEB_MAX_DAILY_POSTS)와 동시실행 방지 잠금(_acquire_lock)이 이미 안전하게
막아주므로, 이미 따라잡았거나 원래 실행이 아직 진행 중이면 아무 일도 안 하고
조용히 끝난다.
"""
import json
import logging
import subprocess
import sys
from datetime import date, datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from config import BASE_DIR, DATA_DIR, LOGS_DIR, CELEB_POST_HOURS
import celeb_db as db

STATE_PATH = DATA_DIR / "celeb_watchdog_state.json"
WATCHDOG_RUN_LOG = LOGS_DIR / "celeb_watchdog_runs.log"
MAX_WATCHDOG_ATTEMPTS = 3


def _expected_count_by_now() -> int:
    """지금까지 지난 슬롯 시각 개수 = 지금 시각 기준 있어야 할 최소 발행 건수."""
    now_hour = datetime.now().hour
    return sum(1 for h in CELEB_POST_HOURS if now_hour >= h)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"date": "", "attempts": 0}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"date": "", "attempts": 0}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    db.init_db()
    expected = _expected_count_by_now()
    actual = db.count_published_today()

    if actual >= expected:
        logger.info(f"[STATUS=WATCHDOG_OK] 지금 시각 기준 기대 {expected}건 / 실제 {actual}건 — 정상")
        return

    today = date.today().isoformat()
    state = _load_state()
    if state.get("date") != today:
        state = {"date": today, "attempts": 0}

    if state["attempts"] >= MAX_WATCHDOG_ATTEMPTS:
        logger.warning(
            f"[STATUS=WATCHDOG_GIVEUP] 기대 {expected}건/실제 {actual}건 미달이지만 "
            f"오늘 재시도 {state['attempts']}회 모두 소진 — 수동 확인 필요"
        )
        return

    state["attempts"] += 1
    _save_state(state)
    logger.warning(
        f"[STATUS=WATCHDOG_RETRY] 기대 {expected}건 / 실제 {actual}건 — 미달분 재시도 "
        f"({state['attempts']}/{MAX_WATCHDOG_ATTEMPTS}회째)"
    )

    # 재시도 실행분의 실제 로그(후보 탐색·생성·발행 결과)는 전용 로그 파일에
    # 남긴다 — 슬롯별 로그(celeb_CelebBlog-Morning.log 등) 중 어디에 속하는지
    # 애매하므로 감시 재시도 전용 파일로 분리한다.
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(WATCHDOG_RUN_LOG, "a", encoding="utf-8") as f:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "run_celeb_pipeline.py")],
            cwd=str(BASE_DIR),
            stdout=f,
            stderr=subprocess.STDOUT,
        )
    logger.info(f"[STATUS=WATCHDOG_DONE] run_celeb_pipeline.py 종료 코드: {result.returncode}")


if __name__ == "__main__":
    main()
