import json, os, urllib.request, urllib.error
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")
TOKEN = os.getenv("BUFFER_ACCESS_TOKEN", "")
API   = "https://api.buffer.com"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Origin": "https://publish.buffer.com",
    "Referer": "https://publish.buffer.com/",
}

# 1. CreatePostInput 필드 조회
q1 = '{ __type(name: "CreatePostInput") { fields { name type { name kind ofType { name kind } } } } }'
req = urllib.request.Request(API, data=json.dumps({"query": q1}).encode(), headers=headers, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        raw = r.read().decode()
    print("=== CreatePostInput raw ===")
    print(raw[:3000])
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")

# 2. schedulingType enum
q2 = '{ __type(name: "SchedulingType") { enumValues { name } } }'
req2 = urllib.request.Request(API, data=json.dumps({"query": q2}).encode(), headers=headers, method="POST")
try:
    with urllib.request.urlopen(req2) as r:
        raw2 = r.read().decode()
    print("\n=== SchedulingType raw ===")
    print(raw2)
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()}")

# 3. mode: shareNow + schedulingType: DIRECT + imageUrls 시도
q3 = f"""
mutation {{
  createPost(input: {{
    channelId: "6a042d82090476fb99149dcf"
    text: "테스트"
    schedulingType: DIRECT
    imageUrls: ["https://raw.githubusercontent.com/smaxi14-alt/minvint/main/%EC%BD%98%ED%85%90%EC%B8%A0/instagram/2026-05-19/slide01.png"]
  }}) {{
    ... on PostActionSuccess {{ post {{ id }} }}
    ... on MutationError {{ message }}
    ... on InvalidInputError {{ message }}
  }}
}}
"""
req3 = urllib.request.Request(API, data=json.dumps({"query": q3}).encode(), headers=headers, method="POST")
try:
    with urllib.request.urlopen(req3) as r:
        raw3 = r.read().decode()
    print("\n=== imageUrls 테스트 ===")
    print(raw3[:500])
except urllib.error.HTTPError as e:
    print(f"\n=== imageUrls 테스트 ===\nHTTP {e.code}: {e.read().decode()[:500]}")
