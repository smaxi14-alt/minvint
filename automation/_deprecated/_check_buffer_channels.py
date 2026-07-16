"""Buffer에 연결된 채널 목록 조회 — 유효한 BUFFER_CHANNEL_ID 확인용"""
import json
import urllib.request
import urllib.error
from config import BUFFER_ACCESS_TOKEN

GRAPHQL_URL = "https://api.buffer.com/graphql"
REST_URL     = "https://api.bufferapp.com/1/profiles.json"

HEADERS_GQL = {
    "Authorization": f"Bearer {BUFFER_ACCESS_TOKEN}",
    "Content-Type": "application/json",
    "Origin": "https://publish.buffer.com",
}

# Buffer GraphQL 채널 쿼리 후보
QUERIES = {
    "channelsList": """query { channelsList { id name service serviceType isConnected } }""",
    "profile":      """query { profile { id name } }""",
}


def _gql(query: str):
    payload = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=HEADERS_GQL, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8")[:300]}


def _rest_profiles():
    url = f"{REST_URL}?access_token={BUFFER_ACCESS_TOKEN}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=== Buffer REST API (profiles) ===")
    result = _rest_profiles()
    if isinstance(result, list):
        for p in result:
            print(f"  id: {p.get('id')}, service: {p.get('service')}, "
                  f"name: {p.get('formatted_username') or p.get('name')}, "
                  f"connected: {not p.get('is_disabled', True)}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n=== Buffer GraphQL 쿼리 시도 ===")
    for name, q in QUERIES.items():
        result = _gql(q)
        print(f"\n[{name}]")
        print(json.dumps(result, indent=2, ensure_ascii=False)[:500])


if __name__ == "__main__":
    main()
