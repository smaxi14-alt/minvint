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
import pygetwindow as gw
import pyautogui
import pyperclip

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


def _markdown_table_rows(table_lines: list[str]) -> list[list[str]]:
    """마크다운 표를 [[셀,셀,...], ...] 행 목록으로 변환 (구분선 제거)."""
    rows = [l.strip() for l in table_lines if l.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^\|[\s:|-]+\|$", r)]  # 구분선(---) 제거
    return [[c.strip() for c in r.strip("|").split("|")] for r in rows]


def _markdown_to_blocks(body: str) -> list[tuple[str, object]]:
    """블로그 본문 마크다운을 (종류, 내용) 블록 목록으로 변환.
    종류: "header"(소제목, 굵게), "para"(일반 문단), "table"(행 목록 list[list[str]])
    """
    lines = body.splitlines()
    blocks: list[tuple[str, object]] = []
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
            blocks.append(("table", _markdown_table_rows(table_block)))
            continue
        elif stripped == "" or stripped == "---":
            pass
        else:
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)  # 굵게 마크다운 기호만 제거
            blocks.append(("para", text))
        i += 1
    return blocks


def _get_latest_table(driver: webdriver.Chrome, timeout: float = 5):
    """문서에 표가 여러 개 있을 수 있으므로 항상 가장 마지막(최신) 표를 반환한다.
    (.se-table은 단수 조회 시 항상 첫 번째 표만 가리켜 두 번째 표부터는
    엉뚱한 표를 조작하게 되는 버그가 있었음)
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        tables = driver.find_elements(By.CSS_SELECTOR, ".se-table")
        if tables:
            return tables[-1]
        time.sleep(0.2)
    raise NoSuchElementException("표 요소를 찾지 못함 (.se-table)")


def _insert_table(driver: webdriver.Chrome, rows: list[list[str]]):
    """실제 SmartEditor 표 컴포넌트를 삽입하고 셀을 채운다.
    표 버튼 클릭 시 기본 3행×3열 표가 생성됨 — 필요한 만큼 행을 늘려서 채운다.
    (열은 이 프로젝트 표 포맷이 항상 3열이라 3열 고정으로 처리)
    """
    table_btn = driver.find_element(By.CSS_SELECTOR, ".se-table-toolbar-button")
    table_btn.click()
    time.sleep(0.6)

    n_rows = len(rows)
    extra_rows_needed = max(0, n_rows - 3)
    for _ in range(extra_rows_needed):
        # 행 추가마다 표 컴포넌트가 재렌더링되어 이전 참조가 stale해질 수 있어
        # 매번 다시 조회한다 (문서에 표가 여러 개면 항상 마지막 표 기준).
        table_el = _get_latest_table(driver)
        row_items = table_el.find_elements(By.CSS_SELECTOR, ".se-cell-controlbar-row .se-cell-controlbar-item")
        add_btn = row_items[-1].find_element(By.CSS_SELECTOR, ".se-cell-add-button")
        # 표 위치에 따라 크기 조절 버튼(.se-cell-size-button)이 같은 좌표에 겹쳐
        # 네이티브 클릭이 가로채이는 경우가 있어 JS로 직접 클릭한다.
        driver.execute_script("arguments[0].click();", add_btn)
        time.sleep(0.3)

    table_el = _get_latest_table(driver)  # 행 추가로 재렌더링됐을 수 있어 재조회
    cells = table_el.find_elements(By.CSS_SELECTOR, "td.se-cell")
    idx = 0
    for row in rows:
        for cell_text in row:
            if idx >= len(cells):
                break
            cells[idx].click()
            time.sleep(0.1)
            driver.switch_to.active_element.send_keys(cell_text)
            idx += 1

    # 표 바로 아래 여백을 클릭해 포커스를 표 밖으로 이동시킨다 (표 자체 기준
    # 상대 좌표라 이전 문단 상태와 무관하게 항상 안정적으로 표 뒤로 이동 가능).
    table_el = _get_latest_table(driver)  # 셀 채우기로 재렌더링됐을 수 있어 재조회
    table_height = table_el.rect["height"]
    ActionChains(driver).move_to_element(table_el).move_by_offset(
        0, int(table_height / 2) + 20
    ).click().perform()
    time.sleep(0.2)


def _type_body(driver: webdriver.Chrome, body_markdown: str):
    """실제 키보드 입력(send_keys)으로 본문을 채운다.
    SmartEditor는 React 기반이라 DOM을 직접 조작(innerHTML)하면 화면에 반영되지 않는다 —
    반드시 실제 키 입력 이벤트를 통해서만 편집기 내부 상태가 갱신된다.
    """
    blocks = _markdown_to_blocks(body_markdown)
    for kind, content in blocks:
        if kind == "table":
            _insert_table(driver, content)
            continue

        active = driver.switch_to.active_element
        if kind == "header":
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
            active.send_keys(content)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
        else:
            active.send_keys(content)
        active_final = driver.switch_to.active_element
        active_final.send_keys(Keys.ENTER)
        time.sleep(0.05)


def _ensure_window_focused(window, attempts: int = 5) -> bool:
    """윈도우에 실제 OS 포커스가 가도록 확인·재시도한다.

    SetForegroundWindow류 API(activate())는 Windows가 백그라운드 프로세스의
    포커스 탈취를 차단하는 경우가 있어 실패할 수 있다 — 이 경우 타이틀바를
    실제 마우스로 클릭한다 (마우스 클릭은 이 제약을 받지 않아 훨씬 안정적).
    """
    for _ in range(attempts):
        active = gw.getActiveWindow()
        if active and active.title == window.title:
            return True

        try:
            window.activate()
        except Exception:
            pass
        time.sleep(0.4)

        active = gw.getActiveWindow()
        if active and active.title == window.title:
            return True

        try:
            click_x = window.left + window.width // 2
            click_y = window.top + 10  # 타이틀바 부근
            pyautogui.click(click_x, click_y)
        except Exception:
            pass
        time.sleep(0.5)

    active = gw.getActiveWindow()
    return bool(active and active.title == window.title)


def _insert_image(driver: webdriver.Chrome, image_path: str, max_retries: int = 3) -> bool:
    """이미지를 업로드한다.

    네이버 블로그 발행 API는 2020년 종료됐고, '사진' 버튼은 웹페이지 안의 파일 입력창이
    아니라 **OS 네이티브 '열기' 대화상자**를 직접 연다 (셀레니움의 표준 send_keys 방식이
    통하지 않는 이유). 이 대화상자에 실제 마우스/키보드 자동화(pyautogui)로 경로를
    입력해 업로드한다.

    주의:
    - 경로에 한글(예: "문서", "마케팅" 폴더명)이 포함되면 pyautogui.write()가
      유니코드를 제대로 입력하지 못해 실패한다 — 반드시 클립보드 붙여넣기로 처리.
    - 대화상자에 실제 OS 포커스가 갔는지 매 단계 확인 후에만 키 입력을 보낸다.
      확인 안 되면 절대 타이핑하지 않고 중단 (다른 창에 잘못 입력되는 사고 방지).
    - OS 포커스 타이밍은 100% 결정적이지 않으므로 실패 시 대화상자를 닫고
      처음부터 재시도한다 (기본 3회). 마지막까지 실패하면 이미지 없이 안전하게
      계속 진행한다 (발행 자체는 막지 않음).
    """
    # 이전 시도에서 남았을 수 있는 동일 제목의 낡은 대화상자와 헷갈리지 않도록 정리
    for stale in gw.getWindowsWithTitle("열기"):
        try:
            stale.close()
        except Exception:
            pass

    for attempt in range(1, max_retries + 1):
        photo_btn = driver.find_element(By.CSS_SELECTOR, ".se-image-toolbar-button")
        photo_btn.click()

        dialog = None
        for _ in range(20):
            matches = gw.getWindowsWithTitle("열기")
            if matches:
                dialog = matches[0]
                break
            time.sleep(0.3)

        if dialog is None:
            logger.warning(f"[naver] 이미지 업로드 시도 {attempt}/{max_retries}: 대화상자를 찾지 못함")
            time.sleep(1)
            continue

        if not _ensure_window_focused(dialog):
            logger.warning(f"[naver] 이미지 업로드 시도 {attempt}/{max_retries}: 포커스 확보 실패 — 재시도")
            try:
                pyautogui.press("esc")
            except Exception:
                pass
            time.sleep(1)
            continue

        pyautogui.hotkey("alt", "n")  # 파일 이름 입력란으로 포커스
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyperclip.copy(image_path)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)

        if not _ensure_window_focused(dialog, attempts=2):
            logger.warning(f"[naver] 이미지 업로드 시도 {attempt}/{max_retries}: 입력 중 포커스 이탈 — 재시도")
            try:
                pyautogui.press("esc")
            except Exception:
                pass
            time.sleep(1)
            continue

        pyautogui.press("enter")
        logger.info(f"[naver] 이미지 업로드 중... (시도 {attempt}/{max_retries})")
        time.sleep(8)  # 업로드 + 편집기 반영 대기

        try:
            driver.find_element(By.CSS_SELECTOR, ".se-components-wrap img")
            logger.info(f"[naver] 이미지 업로드 성공 (시도 {attempt}/{max_retries})")
            return True
        except NoSuchElementException:
            logger.warning(f"[naver] 이미지 업로드 시도 {attempt}/{max_retries}: 업로드 확인 안 됨 — 재시도")
            continue

    logger.warning(f"[naver] 이미지 업로드 {max_retries}회 모두 실패 — 이미지 없이 계속 진행")
    return False


def post_to_naver_blog(
    title: str,
    body_markdown: str,
    tags: list[str],
    image_path: str | None = None,
    dry_run: bool = False,
) -> str:
    """네이버 블로그에 글을 발행한다.

    image_path를 주면 제목 바로 다음에 대표 이미지 1장을 삽입한다 (업로드 실패 시
    경고만 남기고 이미지 없이 계속 진행 — 발행 자체를 막지 않음).
    dry_run=True면 제목/본문을 채우고 스크린샷만 남긴 뒤 발행 버튼은 누르지 않는다.
    (최초 셀렉터 검증용 — 실제 발행 전 반드시 한 번 dry_run으로 확인 권장)
    반환: 발행된 글 URL (dry_run이면 "DRY_RUN")
    """
    # 이미지 업로드는 실제 OS 네이티브 대화상자를 조작해야 해서 화면에 보이는
    # 브라우저 창이 필요하다 — headless로는 동작하지 않는다.
    driver = _build_driver(headless=(dry_run is False and image_path is None))
    try:
        _login(driver)

        driver.get(f"https://blog.naver.com/{NAVER_BLOG_ID}?Redirect=Write")
        wait = WebDriverWait(driver, 15)

        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))

        # "작성 중인 글이 있습니다 — 이어서 작성하시겠습니까?" 팝업 처리.
        # 이 팝업은 mainFrame(iframe) 안에 렌더링되며 약간의 지연 후 나타날 수 있다 —
        # 취소를 눌러 새 글로 시작 (짧게 대기 후 없으면 그냥 진행).
        try:
            popup_cancel = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".se-popup-button-cancel"))
            )
            popup_cancel.click()
            time.sleep(0.5)
        except TimeoutException:
            pass

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

        if image_path:
            if _insert_image(driver, image_path):
                logger.info("[naver] 대표 이미지 삽입 완료")
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
