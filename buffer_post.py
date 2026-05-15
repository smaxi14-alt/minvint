# -*- coding: utf-8 -*-
import json, urllib.request, urllib.error

TOKEN = "90xGRJ47Atkp-tjYEgkaQ_Uj5uQlfhG6fnhO1gd_W2G"
URL = "https://api.buffer.com"
CHANNEL_ID = "6a042d82090476fb99149dcf"

def gql(query):
    data = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(URL, data=data, method="POST", headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Origin": "https://publish.buffer.com",
        "Referer": "https://publish.buffer.com/",
    })
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"HTTP {e.code}: {body}")
        return {}

text = (
    "오늘 재미있는 케이스가 있었어요.\\n\\n"
    "고객 증권 분석하다가 발견했는데\\n"
    "2014년에 가입한 실손이 아직도 살아있더라고요.\\n\\n"
    "현재 실손이랑 완전 중복 구조\\n"
    "매달 28,000원이 10년째 나가고 있었어요.\\n\\n"
    "10년이면 336만원이에요.\\n"
    "증권은 가끔씩 꺼내봐야 하는 이유가 여기 있습니다."
)

mutation = f"""
mutation {{
  createPost(input: {{
    channelId: "{CHANNEL_ID}"
    text: "{text}"
    schedulingType: automatic
    assets: []
    mode: shareNow
  }}) {{
    ... on PostActionSuccess {{
      post {{ id status dueAt }}
    }}
    ... on MutationError {{ message }}
    ... on InvalidInputError {{ message }}
    ... on LimitReachedError {{ message }}
    ... on NotFoundError {{ message }}
    ... on RestProxyError {{ message }}
  }}
}}
"""

result = gql(mutation)
print(json.dumps(result, ensure_ascii=False, indent=2))
