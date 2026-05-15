# 쓰레드 자동 발행 시스템

하루 3개 글을 자동 생성·발행. Claude AI로 글을 만들고 Meta Threads API로 올린다.

---

## 동작 흐름

```
scheduler.py 실행
  ↓ 07:30  → 어그로·공감 글 생성 → Threads 발행
  ↓ 12:30  → 재테크·관찰 글 생성 → Threads 발행
  ↓ 21:00  → 일상·인사이트 글 생성 → Threads 발행
  ↓ logs/posts_log.json에 전체 이력 저장
```

Phase는 `START_DATE` 기준으로 자동 전환됨:
- Phase 1 (1~90일): 어그로·공감 위주
- Phase 2 (91~180일): 재테크 강화
- Phase 3 (181일~): 보험 인사이트 + CTA

---

## 준비물

1. Python 3.10 이상
2. Anthropic API 키
3. Meta Threads 계정 + API 접근 권한

---

## 설치

```bash
# 1. 이 폴더로 이동
cd 마케팅/automation

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정
copy .env.example .env
# .env 파일을 메모장으로 열어 값 채우기
```

---

## Threads API 설정 방법

### Step 1 — Meta Developer 앱 만들기
1. https://developers.facebook.com 접속 → "내 앱" → "앱 만들기"
2. 앱 유형: "기타" → 앱 이름 입력
3. 대시보드에서 **앱 ID**와 **앱 시크릿** 확인 → `.env`의 `THREADS_APP_ID`, `THREADS_APP_SECRET`에 입력

### Step 2 — Threads 권한 추가
1. 왼쪽 메뉴 → "제품 추가" → "Threads API" 추가
2. 권한 요청: `threads_basic`, `threads_content_publish` 체크

### Step 3 — 액세스 토큰 발급
1. Threads API → "API 탐색기" 또는 OAuth 흐름 진행
2. 단기 토큰(1시간) → 장기 토큰(60일) 교환:

```bash
curl -X GET "https://graph.threads.net/access_token" \
  -d "grant_type=th_exchange_token" \
  -d "client_secret=YOUR_APP_SECRET" \
  -d "access_token=SHORT_LIVED_TOKEN"
```

3. 응답의 `access_token` 값 → `.env`의 `THREADS_ACCESS_TOKEN`에 입력

### Step 4 — 사용자 ID 확인
```bash
curl "https://graph.threads.net/v1.0/me?fields=id,username&access_token=YOUR_TOKEN"
```
응답의 `id` 값 → `.env`의 `THREADS_USER_ID`에 입력

---

## 실행

```bash
# automation 폴더에서 실행
cd 마케팅/automation
python scheduler.py
```

창을 닫으면 중단됨. 백그라운드 실행은 아래 옵션 참고.

### 백그라운드 실행 (PC 계속 켜둘 때)

**방법 A — PowerShell 백그라운드**
```powershell
Start-Process python -ArgumentList "scheduler.py" -WorkingDirectory "C:\Users\ant_6\OneDrive\문서\마케팅\automation" -WindowStyle Hidden
```

**방법 B — Windows 작업 스케줄러**
1. 작업 스케줄러 열기 (taskschd.msc)
2. "기본 작업 만들기" → 트리거: "컴퓨터 시작 시"
3. 동작: `python.exe` / 인수: `scheduler.py` / 시작 위치: automation 폴더 경로

---

## 글 미리보기 (발행 전 테스트)

```bash
# 실제 발행 없이 글 내용만 확인
python -c "
from content_generator import generate_post
text, ctype = generate_post('morning')
print(f'[{ctype}]')
print(text)
"
```

---

## 로그 확인

```bash
# 실시간 로그 보기
Get-Content logs/scheduler.log -Wait -Tail 20

# 발행 이력 확인
python -c "
import json
with open('logs/posts_log.json') as f: d=json.load(f)
for p in d['posts'][-5:]:
    print(p['date'], p['slot'], p['content_type'])
    print(p['text'][:80])
    print()
"
```

---

## 파일 구조

```
automation/
├── scheduler.py          ← 메인 실행 파일
├── content_generator.py  ← Claude API로 글 생성
├── threads_poster.py     ← Threads API로 발행
├── content_tracker.py    ← 발행 이력 추적
├── config.py             ← 환경변수 로딩
├── requirements.txt
├── .env                  ← 실제 키 값 (Git에 올리지 말 것)
├── .env.example          ← 템플릿
├── data/
│   ├── content_seeds.json  ← 주제·템플릿 데이터
│   └── token.json          ← 토큰 자동 저장 (자동 생성)
└── logs/
    ├── scheduler.log       ← 실행 로그 (자동 생성)
    └── posts_log.json      ← 발행 이력 (자동 생성)
```

---

## 자주 묻는 질문

**Q. 특정 슬롯을 수동으로 지금 바로 올리고 싶다면?**
```bash
python -c "from scheduler import morning; morning()"
# 또는 noon(), evening()
```

**Q. Phase를 수동으로 바꾸고 싶다면?**
`.env`의 `START_DATE`를 조정하면 됨.  
Phase 2로 바로 넘어가려면 91일 전 날짜로 설정.

**Q. 토큰이 만료됐다는 오류가 나면?**
Threads API에서 새 토큰 발급 후 `.env`와 `data/token.json` 업데이트.

**Q. 글 내용을 수동으로 수정하고 싶다면?**
`data/content_seeds.json`의 `themes` 항목 추가·수정.  
`content_generator.py`의 `PHASE_INSTRUCTIONS` 수정으로 톤 조정 가능.
