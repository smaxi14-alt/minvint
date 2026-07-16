"""1일 1글 발행 감시 — 오늘 아직 발행 안 됐으면 run_daily_blog.py를 재시도한다.

2026-07-16 신설 — 그날 11:00 스케줄러 작업(NaverBlog-Daily)이 로그 한 줄도
남기지 못한 채 exit code 1로 조용히 죽은 사고 대응. Windows 이벤트 로그
(Application/System/Defender 전부 확인)와 Task Scheduler 자체 재시작 설정
(RestartCount=1)까지 점검했지만 정확한 원인은 못 잡았다 — OneDrive 동기화로
로그 파일이 순간적으로 잠겨 cmd.exe의 리다이렉션(`>>`) 자체가 실패했을
가능성이 가장 유력하지만 확정 물증은 없다(재발 시 대조할 수 있도록
Microsoft-Windows-TaskScheduler/Operational 이벤트 로그를 이 조사 과정에서
활성화해뒀다 — 그동안은 비활성 상태라 스케줄러 자체의 실패 사유 기록이
전혀 없었다).

원인을 코드로 직접 막을 수 없으니(스케줄러 실행 순간의 OS/OneDrive 레벨
경합은 애플리케이션 코드가 손댈 수 있는 영역이 아니다), 대신 "오늘 발행이
실제로 됐는가"라는 결과 기준으로 감시해 재시도하는 방식을 택했다.
setup_blog_watchdog_scheduler.ps1이 11:00 이후 하루 중 몇 차례 더(12:30/
15:00/17:30) 이 스크립트를 실행하도록 등록한다 — 매번 이미 발행됐으면
즉시 종료하므로 정상적인 날은 아무 부작용이 없다.

같은 날 이미지 API 장애(2026-07-16 실측: Gemini 503/504)처럼 재시도해도
계속 실패하는 경우를 대비해 하루 최대 재시도 횟수(MAX_WATCHDOG_ATTEMPTS)를
둔다 — 무한정 재시도하며 API 쿼터/시간을 낭비하지 않는다.
"""
import json
import logging
import subprocess
import sys
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

from config import BASE_DIR, DATA_DIR, LOGS_DIR
from blog_tracker import was_posted_today

STATE_PATH = DATA_DIR / "blog_watchdog_state.json"
DAILY_BLOG_LOG = LOGS_DIR / "blog_daily.log"
MAX_WATCHDOG_ATTEMPTS = 3


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
    if was_posted_today():
        logger.info("[STATUS=WATCHDOG_OK] 오늘 이미 발행됨 — 재시도 불필요")
        return

    today = date.today().isoformat()
    state = _load_state()
    if state.get("date") != today:
        state = {"date": today, "attempts": 0}

    if state["attempts"] >= MAX_WATCHDOG_ATTEMPTS:
        logger.warning(
            f"[STATUS=WATCHDOG_GIVEUP] 오늘 재시도 {state['attempts']}회 모두 소진 — "
            f"data/needs_review/{today}.json 확인 필요(있다면 그게 원인)"
        )
        return

    state["attempts"] += 1
    _save_state(state)
    logger.warning(
        f"[STATUS=WATCHDOG_RETRY] 오늘 미발행 감지 — run_daily_blog.py 재실행 "
        f"({state['attempts']}/{MAX_WATCHDOG_ATTEMPTS}회째)"
    )

    # 재시도 실행분의 실제 로그(생성 과정·이미지·발행 결과)는 blog_daily.log에
    # 그대로 이어붙인다 — 정상 스케줄 실행분과 감시 재시도분을 한 파일에서
    # 시간순으로 함께 봐야 그날 무슨 일이 있었는지 추적하기 쉽다. 이 파일
    # 자체(blog_daily_watchdog.log)에는 감시 판단(재시도할지/포기할지)만 남는다.
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(DAILY_BLOG_LOG, "a", encoding="utf-8") as f:
        result = subprocess.run(
            [sys.executable, str(BASE_DIR / "run_daily_blog.py")],
            cwd=str(BASE_DIR),
            stdout=f,
            stderr=subprocess.STDOUT,
        )
    logger.info(f"[STATUS=WATCHDOG_DONE] run_daily_blog.py 종료 코드: {result.returncode}")


if __name__ == "__main__":
    main()
