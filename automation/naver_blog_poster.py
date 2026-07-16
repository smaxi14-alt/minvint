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
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, ElementNotInteractableException,
    ElementClickInterceptedException, StaleElementReferenceException,
)
from webdriver_manager.chrome import ChromeDriverManager
import pygetwindow as gw
import pyautogui
import pyperclip

from config import DATA_DIR, NAVER_BLOG_ID, NAVER_BLOG_PW

logger = logging.getLogger(__name__)

PROFILE_DIR = DATA_DIR / "naver_chrome_profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _build_driver(headless: bool = True, profile_dir: Path | None = None) -> webdriver.Chrome:
    profile_dir = profile_dir or PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--window-size=1280,1000")
    options.add_argument("--lang=ko-KR")
    # 자동화 탐지 최소화 (네이버는 webdriver 플래그에 민감)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")

    # ChromeDriverManager().install()이 브라우저 버전 확인을 위해 내부적으로
    # PowerShell 서브프로세스를 띄우는데, 이게 간헐적으로
    # "PermissionError: [WinError 5] 액세스가 거부되었습니다"로 실패하는 게
    # 실측 확인됐다(2026-07-13~14, 재현 2회 — 백신 실시간 검사 등 OS 레벨
    # 일시적 충돌로 추정, 재시도하면 대부분 바로 성공함). 이 단계에서 실패하면
    # 그 시점까지 만든 글·이미지가 통째로 버려지므로(호출자가 후보를 통째로
    # 스킵함) 여기서 먼저 짧게 재시도한다.
    driver_path = None
    for attempt in range(1, 4):
        try:
            driver_path = ChromeDriverManager().install()
            break
        except PermissionError:
            logger.warning(f"[naver] ChromeDriver 설치 확인 중 일시적 권한 오류 — 재시도 {attempt}/3")
            time.sleep(2)
    if driver_path is None:
        driver_path = ChromeDriverManager().install()  # 마지막 시도 — 실패하면 예외 전파

    service = Service(driver_path)
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


def setup_login(timeout_sec: int = 300, profile_dir: Path | None = None):
    """최초 1회 수동 로그인용 — 브라우저 창을 띄우고 로그인 완료를 자동 감지할 때까지 대기한다.
    (터미널 입력 없이 동작 — 백그라운드로 실행해도 됨)
    실행: python naver_blog_poster.py setup
    profile_dir을 주면 그 계정 전용 Chrome 프로필로 로그인한다(멀티 계정 지원 —
    예: 셀럽 블로그는 CELEB_PROFILE_DIR로 별도 세션 저장, 기존 계정과 분리).
    """
    driver = _build_driver(headless=False, profile_dir=profile_dir)
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


def _login(driver: webdriver.Chrome, blog_id: str | None = None, blog_pw: str | None = None):
    blog_id = blog_id or NAVER_BLOG_ID
    blog_pw = blog_pw or NAVER_BLOG_PW

    if _is_logged_in(driver):
        logger.info("[naver] 저장된 세션으로 로그인 확인됨")
        return

    if not blog_id or not blog_pw:
        raise RuntimeError(
            "네이버 세션이 만료되었고 계정 ID/PW도 없습니다. "
            "setup_login()으로 수동 로그인을 먼저 진행하세요."
        )

    logger.warning("[naver] 저장된 세션 없음 — ID/PW 자동 로그인 시도 (2단계 인증 있으면 실패함)")
    driver.get("https://nid.naver.com/nidlogin.login")
    time.sleep(1)
    # 네이버 로그인 폼은 붙여넣기로 값 주입 (자동입력 방지 우회) — JS로 값 설정 후 이벤트 디스패치
    driver.execute_script(
        "document.getElementById('id').value = arguments[0];"
        "document.getElementById('pw').value = arguments[1];",
        blog_id, blog_pw,
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
    """마크다운 표를 [[셀,셀,...], ...] 행 목록으로 변환 (구분선 제거).
    헤더 셀의 `**굵게**` 마크다운 기호는 제거한다 — 실제 굵게 서식은
    _insert_table()이 편집기에서 별도로 적용한다 (그대로 두면 별표가 그대로 타이핑됨).
    """
    rows = [l.strip() for l in table_lines if l.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^\|[\s:|-]+\|$", r)]  # 구분선(---) 제거
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    return [[re.sub(r"\*\*(.+?)\*\*", r"\1", c) for c in row] for row in cells]


_INLINE_STYLE_RE = re.compile(r"(\*\*.+?\*\*|==.+?==)")


def _parse_inline_segments(text: str) -> list[tuple[str, str]]:
    """한 줄 텍스트를 (조각, 스타일) 세그먼트 목록으로 나눈다. 스타일은
    "plain"/"bold"/"highlight"(굵게+핑크 배경, 2026-07-14 신설 — celeb_generator.py의
    ==text== 마커). stylize=False로 렌더링할 때는 이 세그먼트를 다시 이어붙여
    기존과 동일한 일반 텍스트로 되돌린다(하위 호환)."""
    segments = []
    for part in _INLINE_STYLE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            segments.append((part[2:-2], "bold"))
        elif part.startswith("==") and part.endswith("==") and len(part) >= 4:
            segments.append((part[2:-2], "highlight"))
        else:
            segments.append((part, "plain"))
    return segments or [("", "plain")]


def _markdown_to_blocks(body: str) -> list[tuple[str, object]]:
    """블로그 본문 마크다운을 (종류, 내용) 블록 목록으로 변환.
    종류: "header"/"para" — content는 list[(text, style)] 세그먼트 목록
         (style: plain/bold/highlight, _parse_inline_segments 참조),
         "table"(행 목록 list[list[str]]),
         "image_marker"((style, prompt) — blog_generator.py의 [[IMAGE:style:설명]] 마커),
         "embed_url"(url — celeb_generator.py의 [[EMBED:url]] 마커, YouTube/Instagram
         게시물 URL을 그대로 타이핑하면 네이버 에디터가 자동으로 실제 임베드 카드로
         변환한다, 2026-07-14 실측 확인. 다운로드·재업로드 없이 공식 임베드만 사용)
    """
    lines = body.splitlines()
    blocks: list[tuple[str, object]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        image_match = re.match(r"^\[\[IMAGE:(infographic|photo):(.+?)\]\]$", stripped)
        embed_match = re.match(r"^\[\[EMBED:(https?://\S+?)\]\]$", stripped)
        if stripped.startswith("## "):
            blocks.append(("header", _parse_inline_segments(stripped[3:].strip())))
        elif image_match:
            blocks.append(("image_marker", (image_match.group(1), image_match.group(2).strip())))
        elif embed_match:
            blocks.append(("embed_url", embed_match.group(1)))
        elif stripped.startswith("|"):
            table_block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_block.append(lines[i])
                i += 1
            blocks.append(("table", _markdown_table_rows(table_block)))
            continue
        elif stripped == "":
            pass
        elif stripped == "---":
            # _context/blog-style-guide.md §3 구분선 규칙 — 화제 전환 지점에
            # 텍스트 기반 구분선을 실제로 타이핑한다 (기존엔 빈 줄과 동일하게 무시되던 버그).
            blocks.append(("para", [("─" * 20, "plain")]))
        else:
            blocks.append(("para", _parse_inline_segments(stripped)))
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
            # 표 삽입 직후 주변 문단이 재배치되며 셀 좌표에 다른 요소가 잠깐
            # 겹치는 경우가 있어(ElementClickInterceptedException 실측) 클릭 전에
            # 셀을 뷰포트 중앙으로 스크롤한다. 단, 클릭 자체는 반드시 실제
            # 브라우저 클릭이어야 한다 — JS execute_script 클릭은 이벤트만
            # 발생시킬 뿐 document.activeElement를 옮기지 않아, 바로 다음 줄의
            # active_element.send_keys()가 엉뚱한 곳에 입력되고 셀은 빈 채로
            # 남는 조용한 실패가 실측됐다(표는 생기지만 내용이 전혀 안 채워짐).
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cells[idx])
            cells[idx].click()
            time.sleep(0.1)
            _send_keys_unicode_safe(driver, driver.switch_to.active_element, cell_text)
            idx += 1

    # 셀 입력이 실제로 반영됐는지 확인 — 위 클릭/포커스 방식이 다시 깨져도
    # 빈 표가 조용히 발행되지 않도록 여기서 반드시 걸러낸다.
    table_el = _get_latest_table(driver)
    filled_cells = table_el.find_elements(By.CSS_SELECTOR, "td.se-cell")
    empty_count = sum(1 for c in filled_cells[:idx] if not c.text.strip())
    if empty_count:
        raise RuntimeError(
            f"표 셀 {empty_count}/{idx}개가 비어 있음 — 클릭/포커스 실패로 텍스트가 "
            f"입력되지 않았습니다. 빈 표로 발행하지 않고 중단합니다."
        )

    if rows:
        _bold_header_row(driver, len(rows[0]))

    # 표 바로 아래 여백을 클릭해 포커스를 표 밖으로 이동시킨다 (표 자체 기준
    # 상대 좌표라 이전 문단 상태와 무관하게 항상 안정적으로 표 뒤로 이동 가능).
    table_el = _get_latest_table(driver)  # 셀 채우기로 재렌더링됐을 수 있어 재조회
    # 표가 페이지 아래쪽에 있으면 "표 중심 + 절반 높이" 오프셋이 실제 브라우저
    # 창 밖으로 나가 MoveTargetOutOfBoundsException이 발생할 수 있어(실측),
    # ActionChains로 마우스를 옮기기 전에 표를 뷰포트 중앙으로 스크롤해 둔다.
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", table_el)
    time.sleep(0.2)
    table_height = table_el.rect["height"]
    ActionChains(driver).move_to_element(table_el).move_by_offset(
        0, int(table_height / 2) + 20
    ).click().perform()
    time.sleep(0.2)


def _bold_header_row(driver: webdriver.Chrome, n_cols: int):
    """표의 첫 행(헤더)을 굵게 표시해 본문 행과 구분되게 한다.
    (_context/blog-style-guide.md §7 표 생성 가이드라인 기준)
    """
    table_el = _get_latest_table(driver)
    cells = table_el.find_elements(By.CSS_SELECTOR, "td.se-cell")
    for cell in cells[:n_cols]:
        # 실제 포커스 이동이 필요하므로 JS 클릭이 아닌 실제 클릭을 쓴다
        # (표 셀 입력 루프와 동일한 이유 — 위 주석 참조).
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cell)
        cell.click()
        time.sleep(0.1)
        active = driver.switch_to.active_element
        active.send_keys(Keys.HOME)
        ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.END).key_up(Keys.SHIFT).perform()
        time.sleep(0.05)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
        time.sleep(0.05)


_HIGHLIGHT_COLOR_HEX = "#ffb7de"


# 정렬/배경색 버튼은 "존재하지만 클릭 불가"(ElementNotInteractable) 상태가
# 실측됐다(2026-07-14 — YouTube 임베드 변환 직후 첫 문단에서 재현). 임베드
# 카드가 막 삽입된 직후엔 에디터 레이아웃이 재배치되는 도중이라 툴바 버튼이
# 일시적으로 겹치거나 뷰포트 밖에 있을 수 있다 — 재시도로 대부분 해결된다.
_STYLE_BUTTON_EXCEPTIONS = (
    NoSuchElementException, ElementNotInteractableException,
    ElementClickInterceptedException, StaleElementReferenceException,
)


def _center_align_current_line(driver: webdriver.Chrome, max_retries: int = 3):
    """커서가 위치한 문단을 가운데 정렬한다(2026-07-14 신설 — 셀럽 블로그
    모바일 캡션 스타일 요청 대응). 정렬은 문단 단위 속성이라 텍스트 선택 없이
    커서 위치만으로 적용된다(실측 확인). 실패해도 예외를 전파하지 않는다 —
    서식은 부가 기능이라 발행 자체를 막으면 안 된다.

    실측 발견(2026-07-14, 근본 원인): 정렬 버튼 자체의 CSS 클래스가 "현재
    정렬 상태"를 반영해서 바뀐다 — 처음엔 se-align-left-toolbar-button이지만
    가운데 정렬을 한 번 적용하고 나면 se-align-center-toolbar-button으로
    바뀐다. "align-left"를 고정 셀렉터로 쓰면 두 번째 문단부터 버튼을 아예
    못 찾아 문단마다 계속 실패했다 — 상태와 무관한 안정적 클래스
    (se-property-toolbar-drop-down-button, 이 정렬 드롭다운 버튼 고유의
    래퍼 클래스)로 찾도록 고쳤다."""
    for attempt in range(1, max_retries + 1):
        try:
            align_btn = driver.find_element(
                By.CSS_SELECTOR, '.se-toolbar .se-property-toolbar-drop-down-button[class*="align"]'
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", align_btn)
            align_btn.click()
            time.sleep(0.3)
            center_option = driver.find_element(By.CSS_SELECTOR, ".se-toolbar-option-align-center-button")
            center_option.click()
            time.sleep(0.2)
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.1)
            return
        except _STYLE_BUTTON_EXCEPTIONS as e:
            logger.debug(f"[naver] 가운데 정렬 시도 {attempt}/{max_retries} 실패: {type(e).__name__}")
            try:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            time.sleep(0.4)
    logger.warning("[naver] 가운데 정렬 적용 실패 — 이 문단은 기본 정렬로 남김 (발행은 계속 진행)")


def _apply_background_color(driver: webdriver.Chrome, hex_color: str | None, max_retries: int = 3):
    """선택된 텍스트에 배경색(형광펜)을 적용한다. hex_color가 None이면
    "색상 없음"을 적용해 배경색을 제거한다(다음 세그먼트로 서식이 번지는
    것을 막는 용도, 2026-07-14 신설). 실패해도 예외를 전파하지 않는다."""
    for attempt in range(1, max_retries + 1):
        try:
            bg_btn = driver.find_element(By.CSS_SELECTOR, ".se-toolbar .se-background-color-toolbar-button")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", bg_btn)
            bg_btn.click()
            time.sleep(0.3)
            if hex_color is None:
                swatch = driver.find_element(By.CSS_SELECTOR, "button.se-color-palette-no-color")
            else:
                swatch = driver.find_element(By.CSS_SELECTOR, f"button.se-color-palette[title='{hex_color}']")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", swatch)
            swatch.click()
            time.sleep(0.2)
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.1)
            return
        except _STYLE_BUTTON_EXCEPTIONS as e:
            logger.debug(f"[naver] 배경색 적용 시도 {attempt}/{max_retries} 실패: {type(e).__name__}")
            try:
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            time.sleep(0.4)
    logger.warning("[naver] 배경색 적용 실패 — 이 구간은 서식 없이 남김 (발행은 계속 진행)")


def _has_non_bmp(text: str) -> bool:
    return any(ord(c) > 0xFFFF for c in text)


def _utf16_len(text: str) -> int:
    """브라우저 DOM은 텍스트를 UTF-16 코드 유닛 단위로 다룬다 — BMP 밖 문자
    (대부분의 이모지)는 서로게이트 쌍으로 2유닛을 차지한다. Python의 len()은
    코드포인트 개수라 이모지 1개도 1로 세므로, ARROW_LEFT로 뒤로 선택하는
    글자 수 계산에 그대로 쓰면 이모지가 섞인 구간에서 선택 범위가 어긋난다
    (2026-07-15 실측 발견 — 이모지 지원 추가하면서 같이 확인)."""
    return len(text.encode("utf-16-le")) // 2


def _send_keys_unicode_safe(driver: webdriver.Chrome, element, text: str):
    """ChromeDriver는 BMP(U+0000~U+FFFF) 밖 문자(대부분의 이모지 포함)를
    send_keys()로 보내면 즉시 WebDriverException("only supports characters
    in the BMP")을 던지며 발행 전체가 크래시한다(2026-07-15 실측 — 이모지
    가이드를 celeb_generator.py에 추가한 직후 실발행에서 재현). 그런 문자가
    섞여 있으면 클립보드에 복사해 Ctrl+V로 붙여넣는다 — 이 경로는 WebDriver
    키 전송 프로토콜을 거치지 않는 OS 붙여넣기라 BMP 제약을 받지 않는다
    (이미지 파일 경로 입력에 쓰던 것과 같은 pyperclip 패턴, 641행 참조)."""
    if not _has_non_bmp(text):
        element.send_keys(text)
        return
    pyperclip.copy(text)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
    time.sleep(0.1)


def _type_segments_with_formatting(driver: webdriver.Chrome, segments: list[tuple[str, str]]):
    """(텍스트, 스타일) 세그먼트를 순서대로 타이핑하며 굵게/핑크 배경 서식을
    실제로 적용한다(2026-07-14 신설). 세그먼트마다: 타이핑 → 방금 입력한
    길이만큼 뒤로 선택 → 서식 적용 → 커서를 줄 끝으로 이동하며 서식을
    원상복구(다음 세그먼트로 서식이 이어붙는 것을 막기 위함, 리치텍스트
    에디터에서 흔한 문제)."""
    for text, style in segments:
        if not text:
            continue
        active = driver.switch_to.active_element
        _send_keys_unicode_safe(driver, active, text)
        if style == "plain":
            continue

        # 방금 입력한 길이만큼 한 글자씩 뒤로 선택한다. 전부 한 ActionChains로
        # 묶어서 한 번에 perform()하면 React 기반 에디터가 각 키 이벤트를 다
        # 처리하기 전에 다음 키가 도착해 선택 범위 앞뒤 글자가 빠지는 문제가
        # 실측됐다(2026-07-14) — 키 하나씩 별도 perform()+짧은 대기로 나눠서
        # 에디터가 매 키 입력을 확실히 반영하게 한다. 이모지가 섞이면 UTF-16
        # 코드 유닛 기준으로 세야 선택 범위가 정확하다(_utf16_len 참조).
        for _ in range(_utf16_len(text)):
            ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.ARROW_LEFT).key_up(Keys.SHIFT).perform()
            time.sleep(0.03)

        ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
        if style == "highlight":
            _apply_background_color(driver, _HIGHLIGHT_COLOR_HEX)

        driver.switch_to.active_element.send_keys(Keys.END)
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
        if style == "highlight":
            _apply_background_color(driver, None)


def _type_body(
    driver: webdriver.Chrome, body_markdown: str, image_map: dict[int, str] | None = None,
    stylize: bool = False,
):
    """실제 키보드 입력(send_keys)으로 본문을 채운다.
    SmartEditor는 React 기반이라 DOM을 직접 조작(innerHTML)하면 화면에 반영되지 않는다 —
    반드시 실제 키 입력 이벤트를 통해서만 편집기 내부 상태가 갱신된다.

    image_map: blog_generator.py의 [[IMAGE:style:설명]] 마커 등장 순서(0-indexed)를
    실제 로컬 이미지 파일 경로에 매핑한 dict. 없는 인덱스는 마커만 건너뛰고 계속 진행.

    stylize=True(2026-07-14 신설, 셀럽 블로그 전용): 문단을 가운데 정렬하고
    **굵게**/==핑크 하이라이트== 마커를 실제 서식으로 렌더링한다. 기본값 False는
    기존 동작(마커 제거 후 일반 텍스트, 왼쪽 정렬)을 그대로 유지 — 보험 블로그는
    이 옵션을 쓰지 않아 기존 스타일 가이드(_context/blog-style-guide.md, 좌측
    정렬 두괄식)가 그대로 적용된다.
    """
    blocks = _markdown_to_blocks(body_markdown)
    image_map = image_map or {}
    image_counter = 0
    is_first_block = True
    for kind, content in blocks:
        if kind == "table":
            is_first_block = False
            _insert_table(driver, content)
            continue

        # 2026-07-15 사용자 피드백 — "소제목 시작 전에 2줄 정도 단락 구분이
        # 필요하다": 소제목(##)도 이전엔 다른 문단과 똑같이 Enter 1번만으로
        # 이어져 시각적으로 구분이 안 됐다. 셀럽 블로그(stylize=True)에서만
        # 소제목 앞에 빈 줄을 추가로 하나 더 넣어 섹션 전환처럼 보이게 한다
        # (보험 블로그는 기존 레이아웃 유지를 위해 건드리지 않음).
        if kind == "header" and stylize and not is_first_block:
            driver.switch_to.active_element.send_keys(Keys.ENTER)
            time.sleep(0.05)
        is_first_block = False

        if kind == "image_marker":
            style, prompt = content
            image_path = image_map.get(image_counter)
            if image_path:
                if _insert_image(driver, image_path):
                    logger.info(f"[naver] 이미지 삽입 완료 ({image_counter + 1}번째, style={style})")
                else:
                    logger.warning(f"[naver] 이미지 삽입 실패 — 건너뜀 ({image_counter + 1}번째)")
            else:
                logger.debug(f"[naver] image_map에 {image_counter}번째 항목 없음 — 마커 건너뜀")
            image_counter += 1
            continue

        if kind == "embed_url":
            url = content
            active = driver.switch_to.active_element
            active.send_keys(url)
            time.sleep(0.3)
            active2 = driver.switch_to.active_element
            active2.send_keys(Keys.ENTER)
            # URL 타이핑 직후 네이버가 비동기로 실제 임베드 카드(YouTube 플레이어/
            # Instagram 사진 카드)로 변환한다 — 이 전환이 끝나기 전에 다음 블록을
            # 입력하면 포커스가 엉뚱한 곳으로 튀는 것이 실측됐다(2026-07-14).
            time.sleep(3.0)
            logger.info(f"[naver] URL 자동 임베드 삽입: {url}")
            continue

        active = driver.switch_to.active_element
        if kind == "header":
            flat_text = "".join(seg[0] for seg in content)
            _send_keys_unicode_safe(driver, active, flat_text)
            # _context/blog-style-guide.md 기준: 소제목은 19px + 굵게로 본문(15px)과
            # 위계를 구분한다. 방금 입력한 줄 전체를 선택한 뒤 적용.
            active2 = driver.switch_to.active_element
            active2.send_keys(Keys.HOME)
            ActionChains(driver).key_down(Keys.SHIFT).send_keys(Keys.END).key_up(Keys.SHIFT).perform()
            time.sleep(0.1)
            _set_font_size(driver, 19)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
            if stylize:
                _center_align_current_line(driver)
            # 선택 상태로 Enter를 보내면 텍스트가 지워지므로 커서를 줄 끝으로 이동시켜 선택 해제
            driver.switch_to.active_element.send_keys(Keys.END)
            driver.switch_to.active_element.send_keys(Keys.ENTER)
            # 새로 만들어진 문단이 헤더 서식(굵게+19px)을 그대로 물려받으므로
            # 본문 기본값으로 되돌린다 (되돌리지 않으면 이후 모든 문단이 헤더처럼 보임).
            _set_font_size(driver, 15)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("b").key_up(Keys.CONTROL).perform()
            time.sleep(0.05)
            continue
        else:
            if stylize:
                _type_segments_with_formatting(driver, content)
                _center_align_current_line(driver)
            else:
                flat_text = "".join(seg[0] for seg in content)
                _send_keys_unicode_safe(driver, active, flat_text)
        active_final = driver.switch_to.active_element
        active_final.send_keys(Keys.ENTER)
        time.sleep(0.05)


def _set_font_size(driver: webdriver.Chrome, size: int):
    """현재 선택 영역(또는 커서 위치)에 폰트 크기를 적용한다."""
    try:
        fontsize_btn = driver.find_element(By.CSS_SELECTOR, ".se-font-size-code-toolbar-button")
        fontsize_btn.click()
        time.sleep(0.3)
        size_btn = driver.find_element(By.CSS_SELECTOR, f".se-toolbar-option-font-size-code-fs{size}-button")
        driver.execute_script("arguments[0].click();", size_btn)
        time.sleep(0.2)
    except NoSuchElementException:
        logger.debug(f"[naver] 폰트크기({size}px) 적용 실패 — 건너뜀")


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


def _select_category(driver: webdriver.Chrome, category: str) -> bool:
    """발행 패널의 "카테고리" 드롭다운을 열어 category와 정확히 일치하는
    라벨을 클릭한다. 해당 카테고리는 네이버 블로그 카테고리 관리에 미리
    만들어져 있어야 한다(2026-07-14 실측: kbyoung1120 계정에 "셀럽 패션"/
    "셀럽 다이어트"/"셀럽 뷰티" 서브 카테고리가 이미 생성돼 있음을 확인).
    클래스명은 네이버 빌드마다 해시가 바뀔 수 있어(예: text__sraQE) 안정적인
    보이는 텍스트 기준 XPath로 찾는다. 못 찾으면 False — 호출부는 경고만
    남기고 기본 카테고리로 계속 진행한다(발행 자체를 막지 않음)."""
    try:
        cat_button = driver.find_element(By.XPATH, "//*[text()='카테고리']/following::button[1]")
        cat_button.click()
        time.sleep(0.5)
        option = driver.find_element(By.XPATH, f"//*[text()='{category}']")
        option.click()
        time.sleep(0.3)
        logger.info(f"[naver] 카테고리 선택 완료: {category}")
        return True
    except NoSuchElementException:
        logger.warning(f"[naver] 카테고리 '{category}'를 찾지 못함 — 기본 카테고리로 계속 진행 "
                        f"(네이버 블로그 카테고리 관리에서 미리 만들어져 있어야 함)")
        return False
    except Exception as e:
        logger.warning(f"[naver] 카테고리 선택 실패: {e} — 기본 카테고리로 계속 진행")
        return False


def post_to_naver_blog(
    title: str,
    body_markdown: str,
    tags: list[str],
    image_path: str | None = None,
    image_map: dict[int, str] | None = None,
    dry_run: bool = False,
    blog_id: str | None = None,
    blog_pw: str | None = None,
    profile_dir: Path | None = None,
    category: str | None = None,
    stylize: bool = False,
) -> str:
    """네이버 블로그에 글을 발행한다.

    image_path를 주면 제목 바로 다음에 대표 이미지 1장을 삽입한다 (단일 이미지용,
    하위 호환 목적). image_map을 주면 본문 안의 [[IMAGE:style:설명]] 마커
    등장 순서(0-indexed)에 맞춰 여러 장을 본문 곳곳에 삽입한다 (blog_generator.py의
    다중 이미지 스펙과 연동). 둘 다 줄 수 있음 — image_path는 제목 바로 다음,
    image_map은 본문 마커 위치에 각각 삽입된다.
    업로드 실패 시 경고만 남기고 이미지 없이 계속 진행 (발행 자체를 막지 않음).
    dry_run=True면 제목/본문을 채우고 스크린샷만 남긴 뒤 발행 버튼은 누르지 않는다.
    (최초 셀렉터 검증용 — 실제 발행 전 반드시 한 번 dry_run으로 확인 권장)

    blog_id/blog_pw/profile_dir: 멀티 계정 지원용. 전부 생략하면 기존 전역
    NAVER_BLOG_ID/NAVER_BLOG_PW/PROFILE_DIR을 그대로 쓴다(기존 호출부 회귀 없음).
    다른 계정으로 발행하려면 셋 다 그 계정 값으로 넘긴다(특히 profile_dir을
    다르게 줘야 로그인 세션이 기존 계정과 섞이지 않는다).
    category: 네이버 블로그 카테고리명(예: "셀럽 패션"). 발행 패널의 카테고리
    드롭다운에서 정확히 일치하는 라벨을 찾아 선택한다(2026-07-14 구현 —
    해당 카테고리가 네이버 블로그 카테고리 관리에 미리 만들어져 있어야 함).
    못 찾으면 경고만 남기고 기본 카테고리로 계속 진행한다(발행 자체는 막지 않음).
    stylize: True면 본문을 가운데 정렬 + **굵게**/==핑크 하이라이트==가 실제
    서식으로 반영된 모바일 캡션 스타일로 렌더링한다(2026-07-14, 셀럽 블로그
    전용 — 기본값 False는 기존 보험 블로그 좌측 정렬 스타일 그대로 유지).

    반환: 발행된 글 URL (dry_run이면 "DRY_RUN")
    """

    blog_id = blog_id or NAVER_BLOG_ID
    # 이미지 업로드는 실제 OS 네이티브 대화상자를 조작해야 해서 화면에 보이는
    # 브라우저 창이 필요하다 — headless로는 동작하지 않는다.
    needs_visible = dry_run or image_path is not None or bool(image_map)
    driver = _build_driver(headless=not needs_visible, profile_dir=profile_dir)
    try:
        _login(driver, blog_id=blog_id, blog_pw=blog_pw)

        driver.get(f"https://blog.naver.com/{blog_id}?Redirect=Write")
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
        _send_keys_unicode_safe(driver, active_title, title)
        time.sleep(0.2)
        active_title.send_keys(Keys.ENTER)  # 제목 → 본문 첫 문단으로 포커스 이동
        time.sleep(0.3)

        if image_path:
            if _insert_image(driver, image_path):
                logger.info("[naver] 대표 이미지 삽입 완료")
            time.sleep(0.3)

        _type_body(driver, body_markdown, image_map=image_map, stylize=stylize)

        # 타이핑이 실제로 반영됐는지 확인 (React 기반 에디터라 타이밍 이슈 발생 가능).
        # title_area는 본문 타이핑 전에 잡아둔 참조라, 본문 삽입 중 에디터가
        # 재렌더링되면 stale해져 .text가 빈 값을 반환할 수 있다(2026-07-14 실측 —
        # 실제로는 제목이 정상 입력됐는데도 3회 연속 "반영 안 됨"으로 오판했었다) —
        # 매 시도마다 요소를 새로 조회해서 이 문제를 피한다.
        for attempt in range(3):
            fresh_title_area = driver.find_element(By.CSS_SELECTOR, ".se-title-text .se-text-paragraph")
            if title[:10] in fresh_title_area.text:
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

        if category:
            _select_category(driver, category)

        for tag in tags:
            tag_input = driver.find_element(By.CSS_SELECTOR, ".tag_input__rvUB5")
            _send_keys_unicode_safe(driver, tag_input, tag)
            tag_input.send_keys("\n")
        time.sleep(0.5)

        final_publish_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".confirm_btn__WEaBq")))
        final_publish_btn.click()

        # 클릭 직후 driver.current_url을 바로 잡으면 아직 에디터 페이지
        # (예: "blog.naver.com/smaxi?Redirect=Write&")에 머물러 있는 상태를
        # 실제 게시글 URL로 착각해 "발행 완료"로 잘못 기록할 수 있다(2026-07-14
        # 실측 — 실제로는 미발행인데 성공으로 로그되어 blog_log.json에 가짜
        # 항목이 남았었다). 실제 글번호(logNo)가 붙은 permalink로 리다이렉트될
        # 때까지 최대 10초 폴링하고, 끝내 못 받으면 예외를 던져 호출부가
        # needs_review로 보류하게 한다(가짜 성공 로그 방지).
        post_url_pattern = re.compile(rf"blog\.naver\.com/{re.escape(blog_id)}/\d+")
        post_url = None
        for _ in range(20):
            candidate = driver.current_url
            if post_url_pattern.search(candidate):
                post_url = candidate
                break
            time.sleep(0.5)

        if post_url is None:
            screenshot_path = str(DATA_DIR / "naver_publish_unconfirmed.png")
            driver.save_screenshot(screenshot_path)
            raise RuntimeError(
                f"발행 버튼 클릭 후 실제 게시글 URL을 확인하지 못함 (현재 URL: "
                f"{driver.current_url}) — 스크린샷: {screenshot_path}"
            )

        logger.info(f"[naver] 발행 완료: {post_url}")
        return post_url
    finally:
        driver.quit()


def delete_post(
    log_no: str,
    blog_id: str | None = None,
    blog_pw: str | None = None,
    profile_dir: Path | None = None,
) -> bool:
    """네이버 블로그 글을 영구 삭제한다. **삭제된 글은 복구할 수 없다.**

    사용처: 사람(또는 Claude)이 발행 후 사후 검증에서 결함을 발견했을 때
    (예: 이미지에 임의 텍스트 삽입, 표 데이터 누락 등 발행 전 자동 점검으로는
    못 잡는 문제), 결함 있는 글을 지우고 수정된 버전을 다시 발행하는 흐름의
    첫 단계로 쓴다. 호출 전 반드시 log_no가 지우려는 글이 맞는지 확인할 것 —
    이 함수 자체는 어떤 확인도 요구하지 않고 즉시 삭제를 실행한다.

    blog_id/blog_pw/profile_dir: 멀티 계정 지원용, post_to_naver_blog()와 동일한
    규칙(전부 생략하면 기존 전역 계정 사용). 다른 계정의 글을 지우려면 그 계정의
    profile_dir을 반드시 함께 넘겨야 한다(로그인 세션 분리).

    UI 경로(2026-07-13 실측): 글 보기 페이지(mainFrame iframe 내부)의
    "본문 기타 기능"(⋮) 메뉴 → "삭제하기" → 네이티브 confirm 대화상자
    ("삭제된 글은 복구할 수 없습니다. 삭제하시겠습니까?") → accept().
    """
    blog_id = blog_id or NAVER_BLOG_ID
    driver = _build_driver(headless=False, profile_dir=profile_dir)
    try:
        _login(driver, blog_id=blog_id, blog_pw=blog_pw)
        driver.get(f"https://blog.naver.com/{blog_id}/{log_no}")
        wait = WebDriverWait(driver, 15)
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "mainFrame")))

        menu_btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.btn_overflow_menu")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", menu_btn)
        driver.execute_script("arguments[0].click();", menu_btn)
        time.sleep(0.5)

        del_btn = driver.find_element(By.CSS_SELECTOR, "a._deletePost")
        driver.execute_script("arguments[0].click();", del_btn)

        alert = WebDriverWait(driver, 5).until(EC.alert_is_present())
        alert.accept()
        time.sleep(2)

        logger.info(f"[naver] 글 삭제 완료: logNo={log_no}")
        return True
    except Exception:
        logger.error(f"[naver] 글 삭제 실패: logNo={log_no}", exc_info=True)
        return False
    finally:
        driver.quit()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_login()
    else:
        print("최초 로그인 설정: python naver_blog_poster.py setup")
