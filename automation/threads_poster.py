import json
import time
import logging
import requests
from datetime import datetime, timedelta
from config import THREADS_USER_ID, THREADS_ACCESS_TOKEN, DATA_DIR

logger = logging.getLogger(__name__)

API_BASE = "https://graph.threads.net/v1.0"
TOKEN_FILE = DATA_DIR / "token.json"


def _load_token() -> dict:
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    # 최초 실행 시 .env 토큰을 파일로 저장
    data = {
        "access_token": THREADS_ACCESS_TOKEN,
        "expires_at": (datetime.now() + timedelta(days=60)).isoformat(),
    }
    _save_token(data)
    return data


def _save_token(data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)


def _refresh_token(access_token: str) -> dict:
    r = requests.get(
        "https://graph.threads.net/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": access_token},
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    return {
        "access_token": d["access_token"],
        "expires_at": (datetime.now() + timedelta(seconds=d["expires_in"])).isoformat(),
    }


def _get_valid_token() -> str:
    data = _load_token()
    expires_at = datetime.fromisoformat(data["expires_at"])
    if datetime.now() > expires_at - timedelta(days=7):
        logger.info("Token expiring soon — refreshing...")
        try:
            data = _refresh_token(data["access_token"])
            _save_token(data)
            logger.info("Token refreshed successfully.")
        except Exception as e:
            logger.error(f"Token refresh failed: {e}. Using existing token.")
    return data["access_token"]


def post_to_threads(text: str, retries: int = 3) -> str:
    token = _get_valid_token()
    user_id = THREADS_USER_ID

    for attempt in range(retries):
        try:
            # Step 1: 컨테이너 생성
            r1 = requests.post(
                f"{API_BASE}/{user_id}/threads",
                params={"media_type": "TEXT", "text": text, "access_token": token},
                timeout=20,
            )
            r1.raise_for_status()
            container_id = r1.json()["id"]

            time.sleep(3)  # 발행 전 대기 (Meta 권장)

            # Step 2: 발행
            r2 = requests.post(
                f"{API_BASE}/{user_id}/threads_publish",
                params={"creation_id": container_id, "access_token": token},
                timeout=20,
            )
            r2.raise_for_status()
            return r2.json()["id"]

        except requests.RequestException as e:
            logger.error(f"Post attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                wait = 60 * (attempt + 1)
                logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)

    raise RuntimeError(f"Failed to post after {retries} attempts")
