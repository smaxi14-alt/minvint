import os, secrets, urllib.request, urllib.error, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
TOKEN = os.getenv("BUFFER_ACCESS_TOKEN", "")
API   = "https://api.buffer.com"
path  = Path(r"C:\Users\ant_6\OneDrive\문서\마케팅\콘텐츠\instagram\2026-05-19\slide01.png")

boundary = "----FB" + secrets.token_hex(8)
with open(path, "rb") as f:
    data = f.read()

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="slide01.png"\r\n'
    f"Content-Type: image/png\r\n\r\n"
).encode() + data + f"\r\n--{boundary}--\r\n".encode()

req = urllib.request.Request(
    f"{API}/v1/media/upload",
    data=body,
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(req) as r:
        print("OK:", r.read().decode())
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code} {e.reason}")
    body_err = e.read().decode("utf-8", "replace")
    print(body_err)
