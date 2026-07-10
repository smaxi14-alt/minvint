"""
네이버 블로그 자동 발행 (셀레니움).

네이버 블로그 공식 발행 API는 2020년에 종료되어, 실제 로그인 후
브라우저(SmartEditor)를 직접 조작하는 방식으로만 자동화 가능하다.
반드시 로컬 PC에서만 실행할 것 — 클라우드 IP는 네이버 보안 인증(2단계 인증,
새 기기 확인 등)에 거의 확실히 걸려 로그인 자체가 막힌다.

최초 1회는 setup_login()으로 브라우저 창을 띄워 직접 로그인(2단계 인증 포함)
해야 한다. 이후에는 저장된 프로필(쿠키)로 자동 로그인을 재사용한다.
"""
import re
import time
import logging
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

from config import DATA_DIR, NAVER_BLOG_ID, NAVER_BLOG_PW

logger = logging.getLogger(__name__)

PROFILE_DIR = DATA_DIR / "naver_chrome_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _build_driver(headless: bool = True) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1280,1000")
    options.add_argument("--lang=ko-KR")
    # 자동화 탐지 최소화 (네이버는 webdriver 플래그에 민감)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    return driver


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    driver.get("https://nid.naver.com/user2/help/myInfo")
    time.sleep(1.5)
    return "nidlogin" not in driver.current_url


def setup_login(timeout_sec: int = 300):
    """최초 1회 수동 로그인용 — 브라우저 창을 띄우고 로그인 완료를 자동 감지할 때까지 대기한다.
    (터미널 입력 없이 동작 — 백그라운드로 실행해도 됨)
    실행: python naver_blog_poster.py setup
    """
    driver = _build_driver(headless=False)
    try:
        driver.get("https://nid.naver.com/nidlogin.login")
        print("브라우저 창에서 네이버에 직접 로그인하세요 (2단계 인증 포함).")
        print(f"최대 {timeout_sec}초 동안 로그인 완료를 자동으로 감지합니다...")
        start = time.time()
        while time.time() - start < timeout_sec:
            time.sleep(3)
            if "nidlogin" not in driver.current_url:
                time.sleep(2)  # 리다이렉트 안정화 대기
                print("로그인 감지됨 — 세션 저장 완료. 이제 post_to_naver_blog()가 이 세션을 재사용합니다.")
                return True
        print(f"{timeout_sec}초 동안 로그인이 감지되지 않았습니다. 다시 시도하세요.")
        return False
    finally:
        driver.quit()


def _login(driver: webdriver.Chrome):
    if _is_logged_in(driver):
        logger.info("[naver] 저장된 세션으로 로그인 확인됨")
        return

    if not NAVER_BLOG_ID or not NAVER_BLOG_PW:
        raise RuntimeError(
            "네이버 세션이 만료되었고 NAVER_BLOG_ID/PW도 없습니다. "
            "setup_login()으로 수동 로그인을 먼저 진행하세요."
        )

    logger.warning("[naver] 저장된 세션 없음 — ID/PW 자동 로그인 시도 (2단계 인증 있으면 실패함)")
    driver.get("https://nid.naver.com/nidlogin.login")
    time.sleep(1)
    # 네이버 로그인 폼은 붙여넣기로 값 주입 (자동입력 방지 우회) — JS로 값 설정 후 이벤트 디스패치
    driver.execute_script(
        "document.getElementById('id').value = arguments[0];"
        "document.getElementById('pw').value = arguments[1];",
        NAVER_BLOG_ID, NAVER_BLOG_PW,
    )
    driver.find_element(By.ID, "log.login").click()
    time.sleep(2)

    if not _is_logged_in(driver):
        raise RuntimeError(
            "자동 로그인 실패 (2단계 인증/기기 확인 요구 가능성 높음). "
            "setup_login()으로 수동 로그인 후 다시 시도하세요."
        )
    logger.info("[naver] ID/PW 자동 로그인 성공")


def _markdown_table_to_text(table_lines: list[str]) -> list[str]:
    """마크다운 표를 '항목 | Before | After' 형태의 읽기 쉬운 텍스트 줄 목록으로 변환.
    (실제 SmartEditor 표 컴포넌트 자동 삽입은 자동화 리스크가 높아 1차 버전에서는 텍스트로 대체)
    """
    rows = [l.strip() for l in table_lines if l.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^\|[\s:|-]+\|$", r)]  # 구분선(---) 제거
    lines = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        lines.append(" | ".join(cells))
    return lines


def _markdown_to_blocks(body: str) -> list[tuple[str, str]]:
    """블로그 본문 마크다운을 (종류, 텍스트) 블록 목록으로 변환.
    종류: "header"(소제목, 굵게 처리), "para"(일반 문단), "table"(표 → 텍스트 줄)
    """
    lines = body.splitlines()
    blocks: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("## "):
            blocks.append(("header", stripped[3:].strip()))
        elif stripped.startswith("|"):
            table_block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_block.append(lines[i])
                i += 1
            for row_text in _markdown_table_to_text(table_block):
                blocks.append(("table", row_text))
            continue
        elif stripped == "" or stripped == "---":
            pass
        else:
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)  # 굵게 마크다운 기호만 제거
            blocks.append(("para", text))
        i += 1
    return blocks


def _type_body(driver: webdriver.Chrome, body_markdown: str):
    """실제 키보드 입력(send_keys)으로 본문을 채운다.
    SmartEditor는 React 기반이라 DOM을 직접 조작(innerHTML)하면 화면에 반영되지 않는다 —
    반드시 실제 키 입력 이벤트를 통해서만 편집기 내부 상태가 갱신된다.
    """
    blocks = _markdown_to_blocks(body_markdown)
    for kind, text in blocks:
        active = driver.switch_to.active_element
        if kind == "header":
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
            active.send_keys(text)
            active2 = driver.switch_to.active_element
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
        elif kind == "table":
            active.send_keys(f"▪ {text}")
        else:
            active.send_keys(text)
        active_final = driver.switch_to.active_element
        active_final.send_keys(Keys.ENTER)
        time.sleep(0.05)


def post_to_naver_blog(title: str, body_markdown: str, tags: list[str], dry_run: bool = False) -> str:
    """네이버 블로그에 글을 발행한다.

    dry_run=True면 제목/본문을 채우고 스크린샷만 남긴 뒤 발행 버튼은 누르지 않는다.
    (최초 셀렉터 검증용 — 실제 발행 전 반드시 한 번 dry_run으로 확인 권장)
    반환: 발행된 글 URL (dry_run이면 "DRY_RUN")
    """
    driver = _build_driver(headless=not dry_run)
    try:
        _login(driver)

        driver.get(f"https://blog.naver.com/{NAVER_BLOG_ID}?Redirect=Write")
        wait = WebDriverWait(driver, 15)

        # 작성 중인 글이 있으면 뜨는 확인 팝업 처리
        try:
            popup_cancel = driver.find_element(By.CSS_SELECTOR, ".se-popup-button-cancel")
            popup_cancel.click()
            time.sleep(0.5)
        except NoSuchElementException:
            pass

        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))

        title_area = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".se-title-text .se-text-paragraph"))
        )
        title_area.click()
        time.sleep(0.2)
        active_title = driver.switch_to.active_element
        active_title.send_keys(title)
        time.sleep(0.2)
        active_title.send_keys(Keys.ENTER)  # 제목 → 본문 첫 문단으로 포커스 이동
        time.sleep(0.3)

        _type_body(driver, body_markdown)

        # 타이핑이 실제로 반영됐는지 확인 (React 기반 에디터라 타이밍 이슈 발생 가능)
        for attempt in range(3):
            if title[:10] in title_area.text:
                break
            logger.warning(f"[naver] 제목 반영 확인 실패 — 재확인 중 ({attempt + 1}/3)")
            time.sleep(1)
        else:
            logger.warning("[naver] 제목이 화면에 반영되지 않은 것으로 보입니다. 스크린샷을 확인하세요.")

        if dry_run:
            screenshot_path = str(DATA_DIR / "naver_dry_run.png")
            driver.save_screenshot(screenshot_path)
            logger.info(f"[naver] dry_run 완료 — 발행하지 않음. 스크린샷: {screenshot_path}")
            return "DRY_RUN"

        # 발행 버튼은 mainFrame(iframe) 안에 있음 — 컨텍스트 전환하지 않는다
        publish_open_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".publish_btn__m9KHH")))
        publish_open_btn.click()
        time.sleep(1)

        for tag in tags:
            tag_input = driver.find_element(By.CSS_SELECTOR, ".tag_input__rvUB5")
            tag_input.send_keys(tag)
            tag_input.send_keys("\n")
        time.sleep(0.5)

        final_publish_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".confirm_btn__WEaBq")))
        final_publish_btn.click()
        time.sleep(2)

        post_url = driver.current_url
        logger.info(f"[naver] 발행 완료: {post_url}")
        return post_url
    finally:
        driver.quit()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_login()
    else:
        print("최초 로그인 설정: python naver_blog_poster.py setup")
