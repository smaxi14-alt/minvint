"""Threads Insights API — 게시물 지표 수집"""
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime

logger = logging.getLogger(__name__)

THREADS_API = "https://graph.threads.net/v1.0"
METRICS = "views,likes,replies,reposts,quotes"


class ThreadsAnalytics:
    def __init__(self, access_token: str):
        self.token = access_token

    def _get(self, path: str, params: dict = None) -> dict:
        p = {"access_token": self.token}
        if params:
            p.update(params)
        url = f"{THREADS_API}{path}?{urllib.parse.urlencode(p)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read())

    def find_media_id(self, posted_at: str, text: str) -> str | None:
        """게시 시간 ±10분 범위에서 Threads 미디어 ID 검색"""
        try:
            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except Exception:
            return None

        since = int(dt.timestamp()) - 600
        until = int(dt.timestamp()) + 600

        try:
            data = self._get("/me/threads", {
                "fields": "id,text,timestamp",
                "since": since,
                "until": until,
                "limit": 10,
            })
        except Exception as e:
            logger.warning(f"Threads 미디어 검색 실패: {e}")
            return None

        items = data.get("data", [])
        text_prefix = text[:25]

        # 텍스트 앞부분 매칭 우선
        for item in items:
            if item.get("text", "")[:25] == text_prefix:
                return item["id"]

        # 매칭 실패 시 시간 범위 내 첫 번째 항목
        return items[0]["id"] if items else None

    def get_insights(self, media_id: str) -> dict:
        """특정 게시물 인사이트 조회"""
        data = self._get(f"/{media_id}/insights", {"metric": METRICS})
        result = {}
        for item in data.get("data", []):
            val = (
                item["values"][0]["value"]
                if item.get("values")
                else item.get("total_value", {}).get("value", 0)
            )
            result[item["name"]] = val
        return result

    def refresh_token(self) -> str:
        """토큰 갱신 (만료 7일 전 실행 권장)"""
        data = self._get("/refresh_access_token", {
            "grant_type": "th_refresh_token",
            "access_token": self.token,
        })
        new_token = data.get("access_token", self.token)
        logger.info(f"토큰 갱신 완료 (유효: {data.get('expires_in', 0)//86400}일)")
        return new_token
