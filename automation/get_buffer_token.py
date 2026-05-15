"""
Buffer OAuth 액세스 토큰 발급 스크립트
실행: python get_buffer_token.py

사전 준비:
1. https://account.buffer.com/developers 접속
2. "Create a new app" 클릭
3. Redirect URI에 정확히 입력: http://127.0.0.1:8888/callback
4. Client ID와 Client Secret 복사 후 아래 입력
"""
import webbrowser
import urllib.parse
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

REDIRECT_URI = "http://127.0.0.1:8888/callback"
code_holder = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if "/callback" in self.path:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code = qs.get("code", [""])[0]
            if code:
                code_holder.append(code)
            self.send_response(200)
            self.end_headers()
            self.wfile.write("<h1>인증 완료! 이 창을 닫고 터미널로 돌아가세요.</h1>".encode("utf-8"))

    def log_message(self, *args):
        pass


def main():
    print("=" * 50)
    print("Buffer OAuth 토큰 발급")
    print("=" * 50)
    print()
    print("준비 사항:")
    print("  https://account.buffer.com/developers 에서 앱 생성 후")
    print(f"  Redirect URI: {REDIRECT_URI}")
    print()

    client_id = input("Client ID 입력: ").strip()
    client_secret = input("Client Secret 입력: ").strip()

    auth_url = (
        f"https://bufferapp.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&response_type=code"
    )

    print()
    print("브라우저에서 Buffer 로그인 후 앱 승인 → 자동으로 토큰 캡처됩니다.")
    input("준비되면 Enter 누르기...")
    webbrowser.open(auth_url)

    server = HTTPServer(("127.0.0.1", 8888), _Handler)
    server.handle_request()  # 콜백 1회 수신 후 종료

    if not code_holder:
        print("코드 수신 실패. 다시 시도하세요.")
        return

    # 코드 → 액세스 토큰 교환
    r = requests.post(
        "https://api.bufferapp.com/1/oauth2/token.json",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "code": code_holder[0],
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    r.raise_for_status()
    access_token = r.json()["access_token"]

    print()
    print("=" * 50)
    print("액세스 토큰 발급 완료!")
    print("=" * 50)
    print()
    print("아래 값을 automation/.env 에 추가하세요:")
    print()
    print(f"BUFFER_ACCESS_TOKEN={access_token}")
    print()

    # 연결된 프로필 목록 자동 출력
    print("연결된 소셜 계정 목록 (BUFFER_PROFILE_ID 확인용):")
    pr = requests.get(
        f"https://api.bufferapp.com/1/profiles.json?access_token={access_token}",
        timeout=10,
    )
    if pr.status_code == 200:
        for p in pr.json():
            service = p.get("service", "?")
            username = p.get("service_username", "?")
            pid = p["id"]
            print(f"  [{service:15s}] {username:25s}  id={pid}")
        print()
        print("Threads 프로필의 id 값을 복사해서 .env에 추가:")
        print("BUFFER_PROFILE_ID=<위 id 값>")
    else:
        print(f"  프로필 조회 실패 (status={pr.status_code})")

    # .env 자동 저장 여부 확인
    print()
    save = input(".env 파일에 자동 저장할까요? (y/n): ").strip().lower()
    if save == "y":
        env_path = Path(__file__).parent / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        # 기존 키 교체 또는 추가
        keys_to_update = {"BUFFER_ACCESS_TOKEN": access_token}
        updated = set()
        new_lines = []
        for line in lines:
            key = line.split("=")[0].strip()
            if key in keys_to_update:
                new_lines.append(f"{key}={keys_to_update[key]}")
                updated.add(key)
            else:
                new_lines.append(line)
        for key, val in keys_to_update.items():
            if key not in updated:
                new_lines.append(f"{key}={val}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"저장 완료: {env_path}")
        print("BUFFER_PROFILE_ID는 위 목록에서 복사해 직접 추가하세요.")


if __name__ == "__main__":
    main()
