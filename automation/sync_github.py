"""
automation/.env → GitHub Secrets / Variables 동기화 스크립트
.env 수정 후 자동 호출됨 (CLAUDE.md 규칙에 의해)
수동 실행: python automation/sync_github.py
"""
import subprocess
from pathlib import Path
from dotenv import dotenv_values

ENV_PATH = Path(__file__).parent / ".env"

# GitHub Secrets (민감 정보 — 마스킹됨)
SECRETS = [
    "ANTHROPIC_API_KEY",
    "BUFFER_ACCESS_TOKEN",
    "BUFFER_CHANNEL_ID",
    "THREADS_ACCESS_TOKEN",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
    "GOOGLE_API_KEY",
]

# GitHub Variables (비민감 설정값 — Actions 로그에 노출됨)
VARIABLES = [
    "START_DATE",
    "YOUTUBE_CHANNELS_PRIMARY",
    "YOUTUBE_CHANNELS_FALLBACK",
]


def _run(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, (r.stderr or r.stdout).strip()


def sync():
    if not ENV_PATH.exists():
        print(f"ERROR: {ENV_PATH} 없음")
        return

    env = dotenv_values(ENV_PATH)
    errors = []

    print("=== GitHub Secrets 동기화 ===")
    for key in SECRETS:
        val = env.get(key)
        if not val:
            print(f"  SKIP  {key}")
            continue
        code, msg = _run(["gh", "secret", "set", key, "--body", val])
        if code == 0:
            print(f"  OK    {key}")
        else:
            print(f"  FAIL  {key}: {msg}")
            errors.append(key)

    print("\n=== GitHub Variables 동기화 ===")
    for key in VARIABLES:
        val = env.get(key)
        if not val:
            print(f"  SKIP  {key}")
            continue
        code, msg = _run(["gh", "variable", "set", key, "--body", val])
        if code == 0:
            print(f"  OK    {key}")
        else:
            print(f"  FAIL  {key}: {msg}")
            errors.append(key)

    print()
    if errors:
        print(f"FAIL: {', '.join(errors)}")
        raise SystemExit(1)
    else:
        print("모든 항목 동기화 완료 OK")


if __name__ == "__main__":
    sync()
