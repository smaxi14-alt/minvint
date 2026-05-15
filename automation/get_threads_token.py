"""
Threads 단기 토큰 → 장기 토큰 변환 (60일 유효)
Usage: python get_threads_token.py
"""
import urllib.request
import urllib.parse
import json
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

SHORT_TOKEN = input("단기 Threads 토큰 붙여넣기: ").strip()
APP_ID      = input("Meta 앱 ID: ").strip()
APP_SECRET  = input("Meta 앱 시크릿: ").strip()

url = (
    "https://graph.threads.net/access_token"
    f"?grant_type=th_exchange_token"
    f"&client_id={APP_ID}"
    f"&client_secret={APP_SECRET}"
    f"&access_token={urllib.parse.quote(SHORT_TOKEN)}"
)

with urllib.request.urlopen(url) as res:
    data = json.loads(res.read())

long_token = data.get("access_token", "")
expires_in = data.get("expires_in", 0)

print(f"\n장기 토큰 (유효: {expires_in // 86400}일):")
print(long_token)
print("\n.env에 아래 내용 추가:")
print(f"THREADS_ACCESS_TOKEN={long_token}")
print(f"THREADS_APP_ID={APP_ID}")
print(f"THREADS_APP_SECRET={APP_SECRET}")
