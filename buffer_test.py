# -*- coding: utf-8 -*-
import json
import urllib.request
import urllib.error

TOKEN = "90xGRJ47Atkp-tjYEgkaQ_Uj5uQlfhG6fnhO1gd_W2G"
URL = "https://api.buffer.com"

def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=data,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Origin": "https://publish.buffer.com",
            "Referer": "https://publish.buffer.com/",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"  HTTP {e.code}: {body or '(empty)'}")
        return {}

# 0. 현재 사용자 정보 확인
print("=== 현재 사용자 / 조직 확인 ===")
result0 = gql("""
{
  account {
    id
    email
    organizations {
      id
      name
    }
  }
}
""")
print(json.dumps(result0, ensure_ascii=False, indent=2))

# 1. 사용 가능한 mutation 목록 (post 관련)
print("\n=== 포스팅 관련 Mutation 탐색 ===")
introspect = gql("""
{
  __schema {
    mutationType {
      fields(includeDeprecated: false) {
        name
        description
      }
    }
  }
}
""")
mutations = introspect.get("data", {}).get("__schema", {}).get("mutationType", {}).get("fields", [])
post_mutations = [m for m in mutations if any(k in m["name"].lower() for k in ["post", "thread", "publish", "schedule", "create"])]
for m in post_mutations:
    print(f"  {m['name']}: {m.get('description', '')}")

# 2. createPost 입력 타입 확인
print("\n=== createPost 입력 스키마 ===")
schema = gql("""
{
  __type(name: "CreatePostInput") {
    name
    inputFields {
      name
      description
      type {
        name
        kind
        ofType { name kind }
      }
    }
  }
}
""")
fields = schema.get("data", {}).get("__type", {}).get("inputFields", [])
for f in fields:
    t = f["type"]
    type_name = t.get("name") or (t.get("ofType") or {}).get("name", "")
    print(f"  {f['name']} ({t['kind']}/{type_name}): {f.get('description','')}")

# 3. 열거값 확인
for enum_name in ["SchedulingType", "ShareMode"]:
    r = gql(f'{{ __type(name: "{enum_name}") {{ enumValues {{ name }} }} }}')
    vals = [v["name"] for v in (r.get("data", {}).get("__type") or {}).get("enumValues", [])]
    print(f"  {enum_name}: {vals}")

# ── 실제 포스팅 ──────────────────────────────────────────────
CHANNEL_ID = "6a042d82090476fb99149dcf"

thread_text = """보험 증권 꺼내본 지 얼마나 됐어요?

오늘 고객 분 증권 살펴봤는데
10년 전에 가입한 실손이 그대로였어요.

지금 실손이랑 중복이라
매달 3만원을 그냥 태우고 계신 거더라고요.

증권, 1년에 한 번은 꺼내봐야 해요.
숫자가 달라져 있거든요."""

print("\n=== Threads 포스팅 시도 ===")
post_result = gql("""
mutation {
  createPost(input: {
    channelId: "6a042d82090476fb99149dcf"
    text: "보험 증권 꺼내본 지 얼마나 됐어요?\\n\\n오늘 고객 분 증권 살펴봤는데\\n10년 전에 가입한 실손이 그대로였어요.\\n\\n지금 실손이랑 중복이라\\n매달 3만원을 그냥 태우고 계신 거더라고요.\\n\\n증권, 1년에 한 번은 꺼내봐야 해요.\\n숫자가 달라져 있거든요."
    schedulingType: automatic
    assets: []
    mode: shareNow
  }) {
    ... on PostActionSuccess {
      post { id status dueAt }
    }
    ... on MutationError { message }
    ... on InvalidInputError { message }
    ... on LimitReachedError { message }
    ... on NotFoundError { message }
    ... on RestProxyError { message }
  }
}
""")
print(json.dumps(post_result, ensure_ascii=False, indent=2))

# 4. 채널 목록 (account 경로로)
print("\n=== 연결된 채널 목록 ===")
result1 = gql("""
{
  account {
    organizations {
      id
      channels {
        id
        name
        service
      }
    }
  }
}
""")
print(json.dumps(result1, ensure_ascii=False, indent=2))
