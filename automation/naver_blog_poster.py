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


def setup_login():
    """최초 1회 수동 로그인용 — 브라우저 창을 띄우고 대기한다.
    실행: python -c "from naver_blog_poster import setup_login; setup_login()"
    """
    driver = _build_driver(headless=False)
    try:
        driver.get("https://nid.naver.com/nidlogin.login")
        print("브라우저 창에서 네이버에 직접 로그인하세요 (2단계 인증 포함).")
        print("로그인 완료 후 이 터미널에서 Enter를 누르면 세션이 저장되고 종료됩니다.")
        input()
        if _is_logged_in(driver):
            print("로그인 세션 저장 완료. 이제 post_to_naver_blog()가 자동 로그인을 재사용합니다.")
        else:
            print("경고: 로그인이 확인되지 않았습니다. 다시 시도하세요.")
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


def _markdown_table_to_html(table_lines: list[str]) -> str:
    rows = [l.strip() for l in table_lines if l.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^\|[\s:|-]+\|$", r)]  # 구분선(---) 제거
    html = ["<table style=\"border-collapse:collapse;width:100%\">"]
    for i, row in enumerate(rows):
        cells = [c.strip() for c in row.strip("|").split("|")]
        tag = "th" if i == 0 else "td"
        style = "border:1px solid #ddd;padding:8px;text-align:left;"
        html.append("<tr>" + "".join(f"<{tag} style='{style}'>{c}</{tag}>" for c in cells) + "</tr>")
    html.append("</table>")
    return "".join(html)


def _markdown_to_html(body: str) -> str:
    """블로그 본문 마크다운(##, 표, 굵게)을 SmartEditor에 넣을 간단한 HTML로 변환."""
    lines = body.splitlines()
    html_parts = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("## "):
            html_parts.append(f"<h3>{stripped[3:].strip()}</h3>")
        elif stripped.startswith("|"):
            table_block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_block.append(lines[i])
                i += 1
            html_parts.append(_markdown_table_to_html(table_block))
            continue
        elif stripped == "" or stripped == "---":
            pass
        else:
            text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", stripped)
            html_parts.append(f"<p>{text}</p>")
        i += 1
    return "".join(html_parts)


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
        driver.execute_script("arguments[0].innerText = arguments[1];", title_area, title)
        driver.execute_script(
            "arguments[0].dispatchEvent(new InputEvent('input', {bubbles: true}));", title_area
        )

        body_area = driver.find_element(By.CSS_SELECTOR, ".se-main-container")
        html = _markdown_to_html(body_markdown)
        driver.execute_script(
            "arguments[0].querySelector('.se-component-content, .se-text-paragraph').innerHTML = arguments[1];",
            body_area, html,
        )
        driver.execute_script(
            "arguments[0].dispatchEvent(new InputEvent('input', {bubbles: true}));", body_area
        )

        if dry_run:
            screenshot_path = str(DATA_DIR / "naver_dry_run.png")
            driver.save_screenshot(screenshot_path)
            logger.info(f"[naver] dry_run 완료 — 발행하지 않음. 스크린샷: {screenshot_path}")
            return "DRY_RUN"

        driver.switch_to.default_content()
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
