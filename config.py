import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = os.getenv("API_ID", "").strip()
API_HASH = os.getenv("API_HASH", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
DOWNLOAD_DIR = BASE_DIR / "downloads"

MAX_UPLOAD_BYTES = 50_000_000
MAX_STORAGE_FILE_BYTES = 2 * 1024 * 1024 * 1024
DOWNLOAD_TIMEOUT = (10, 30)
UPLOAD_TIMEOUT = {
    "connect_timeout": 10,
    "read_timeout": 30,
    "write_timeout": 120,
    "pool_timeout": 10,
}
MAX_REDIRECTS = 5