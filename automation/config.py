import os
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

load_dotenv(BASE_DIR / ".env")

ANTHROPIC_API_KEY           = os.getenv("ANTHROPIC_API_KEY", "")
NAVER_CLIENT_ID             = os.getenv("NAVER_CLIENT_ID", "").lstrip('﻿').strip()
NAVER_CLIENT_SECRET         = os.getenv("NAVER_CLIENT_SECRET", "").lstrip('﻿').strip()
BUFFER_ACCESS_TOKEN         = os.getenv("BUFFER_ACCESS_TOKEN", "")
BUFFER_CHANNEL_ID           = os.getenv("BUFFER_CHANNEL_ID", "")
BUFFER_INSTAGRAM_CHANNEL_ID = os.getenv("BUFFER_INSTAGRAM_CHANNEL_ID", "")

INSTAGRAM_HOUR   = int(os.getenv("INSTAGRAM_HOUR",   "9"))
INSTAGRAM_MINUTE = int(os.getenv("INSTAGRAM_MINUTE", "0"))

START_DATE_STR = os.getenv("START_DATE", date.today().isoformat())
START_DATE = date.fromisoformat(START_DATE_STR)

MORNING_HOUR = int(os.getenv("MORNING_HOUR", "7"))
MORNING_MINUTE = int(os.getenv("MORNING_MINUTE", "30"))
NOON_HOUR = int(os.getenv("NOON_HOUR", "12"))
NOON_MINUTE = int(os.getenv("NOON_MINUTE", "30"))
EVENING_HOUR = int(os.getenv("EVENING_HOUR", "21"))
EVENING_MINUTE = int(os.getenv("EVENING_MINUTE", "0"))
