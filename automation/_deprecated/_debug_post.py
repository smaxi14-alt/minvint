"""Buffer 연결된 채널 목록 조회"""
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

query = """
{
  channels {
    id
    name
    service
    serviceType
    isConnected
  }
}
"""

payload = json.dumps({"query": query}).encode()
req = urllib.request.Request(API, data=payload, headers=headers, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read())
    channels = result.get("data", {}).get("channels", [])
    print("=== Buffer 연결 채널 목록 ===")
    for ch in channels:
        print(f"  id={ch.get('id')}  service={ch.get('service')}  type={ch.get('serviceType')}  name={ch.get('name')}  connected={ch.get('isConnected')}")
    if not channels:
        print(json.dumps(result, ensure_ascii=False, indent=2))
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:600]}")
except Exception as e:
    print(f"오류: {e}")
