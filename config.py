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
_TELETHON_UPLOAD_PART_SIZES_KB = {32, 64, 128, 256, 512}


def _parse_telethon_upload_part_size_kb(value: str | None) -> int:
    if value is None or not value.strip():
        return 512

    try:
        part_size_kb = int(value)
    except ValueError:
        raise ValueError(
            "TELETHON_UPLOAD_PART_SIZE_KB must be one of 32, 64, 128, 256, or 512."
        ) from None

    if part_size_kb not in _TELETHON_UPLOAD_PART_SIZES_KB:
        raise ValueError(
            "TELETHON_UPLOAD_PART_SIZE_KB must be one of 32, 64, 128, 256, or 512."
        )

    return part_size_kb


TELETHON_UPLOAD_PART_SIZE_KB = _parse_telethon_upload_part_size_kb(
    os.getenv("TELETHON_UPLOAD_PART_SIZE_KB")
)
DOWNLOAD_TIMEOUT = (10, 30)
UPLOAD_TIMEOUT = {
    "connect_timeout": 10,
    "read_timeout": 30,
    "write_timeout": 120,
    "pool_timeout": 10,
}
MAX_REDIRECTS = 5