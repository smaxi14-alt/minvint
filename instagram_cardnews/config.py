r"""인스타그램 카드뉴스 자동화 전용 설정 — automation/ 폴더의 파이썬 코드는
전혀 import하지 않는다(2026-07-16, 사용자 명시적 요청: "쓰레드 로직은 지금
완벽하게 구현 중이니 건드리지 말고, 인스타 자동발행은 완전히 다른 폴더로").

2026-07-16 추가 변경 — automation/이 OneDrive 동기화 폴더(ReparsePoint) 안에
있어 Windows 작업 스케줄러가 프로세스를 띄울 때 OneDrive 동기화 엔진과
경합해 스케줄 작업마다 조용히 실패(exit 1 / STATUS_CONTROL_C_EXIT / Win32
267)하는 문제가 실측 확인됐다. 사용자가 automation/ 전체를 OneDrive 밖
순수 로컬 경로 C:\BlogAutomation으로 이전해 이제 그쪽이 유일한 실행
워킹디렉토리다. 이 폴더(instagram_cardnews)도 같은 문제를 겪을 것이므로
동일하게 C:\InstagramCardNews(순수 로컬)에 실행 사본을 두고, 코드 편집은
계속 이 OneDrive git 저장소에서 한다 — **수정 후 반드시 C:\InstagramCardNews
로 다시 복사해야 실제 스케줄러 실행에 반영된다.**

시크릿(BUFFER_IG_*)은 C:\BlogAutomation\.env 파일 경로를 그대로 재사용한다
— "코드 의존"이 아니라 "같은 물리 파일을 읽는 것"뿐이다. Buffer 무료
플랜이 계정당 채널 1개만 허용해 쓰레드용(BUFFER_ACCESS_TOKEN)과는 완전히
다른 Buffer 계정에서 별도 발급받은 크리덴셜이다."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# 쓰레드/블로그 파이프라인이 실제로 발행 완료한 뒤 남기는 로그 — 읽기 전용
# 참조. 파이썬 코드는 import하지 않고 파일 경로로만 접근한다. 2026-07-16부터
# 실행 워킹디렉토리가 C:\BlogAutomation(OneDrive 밖 순수 로컬)로 고정됐다.
AUTOMATION_DIR = Path(r"C:\BlogAutomation")
THREADS_LOG = AUTOMATION_DIR / "logs" / "posts_log.json"
BLOG_LOG = AUTOMATION_DIR / "logs" / "blog_log.json"
BLOG_LAST_RUN_POST = AUTOMATION_DIR / "data" / "last_run_post.json"

# 같은 .env 파일을 공유(값은 C:\BlogAutomation\.env에만 저장, 여기서는 읽기만)
load_dotenv(AUTOMATION_DIR / ".env")

BUFFER_IG_ACCESS_TOKEN    = os.getenv("BUFFER_IG_ACCESS_TOKEN", "")
BUFFER_IG_ORGANIZATION_ID = os.getenv("BUFFER_IG_ORGANIZATION_ID", "")
BUFFER_IG_CHANNEL_ID      = os.getenv("BUFFER_IG_CHANNEL_ID", "")

# 카드뉴스 이미지를 공개 URL로 노출하기 위한 GitHub raw 호스팅 — Buffer의
# media 필드는 로컬 파일 업로드가 아니라 공개 URL을 요구한다(2026-07-16
# 실측 확인, "Unsupported Content-Type" 오류로 REST 업로드 방식이 작동하지
# 않음을 발견). 이 저장소가 이미 공개(public)이고, 과거에도 같은 방식
# (콘텐츠/instagram/2026-05-19/slide0N.png 커밋)으로 카드뉴스 이미지를
# 공개 호스팅한 전례가 있다.
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/smaxi14-alt/minvint/main"

# 카드뉴스 이미지(콘텐츠/[시리즈명]/*.png)는 계속 OneDrive git 저장소에
# 둔다(2026-07-16 사용자 확정 — automation/ 코드만 OneDrive 밖으로 옮기고,
# 콘텐츠는 그대로). 이 폴더(instagram_cardnews) 자체는 실행 시
# C:\InstagramCardNews(순수 로컬)에서 돌아가 git 저장소 하위가 아니므로,
# poster.py의 git 커밋 대상 경로를 여기서 절대경로로 명시한다.
REPO_ROOT = Path(r"c:\Users\ant_6\OneDrive\문서\마케팅")
