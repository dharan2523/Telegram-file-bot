import base64
import logging
import os
import time
from pathlib import Path
from typing import Any

from telethon import TelegramClient, utils
from telethon.tl.custom.message import Message as TelethonMessage
from telethon.tl.types import Channel, DocumentAttributeFilename

from config import API_HASH, API_ID, BASE_DIR, CHANNEL_ID


logger = logging.getLogger(__name__)

SESSION_DIR = BASE_DIR / "sessions"
SESSION_PATH = SESSION_DIR / "telegram_user"


class TelegramStorageError(Exception):
    """Raised for safe-to-display Telethon storage errors."""


def restore_session_from_environment() -> None:
    """
    Restore the Telethon session file from TELEGRAM_SESSION_BASE64.

    Used by hosted environments such as Koyeb.
    Local development continues to use the existing session file
    when this environment variable is not present.
    """
    session_base64 = os.getenv("TELEGRAM_SESSION_BASE64")

    if session_base64 is None:
        logger.info("TELEGRAM_SESSION_BASE64 is NOT set.")
        return

    logger.info(
        "TELEGRAM_SESSION_BASE64 is set. Length=%s",
        len(session_base64),
    )

    if not session_base64:
        return

    session_file = Path(f"{SESSION_PATH}.session")

    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)

        session_bytes = base64.b64decode(
            session_base64,
            validate=True,
        )

        logger.info(
            "Decoded Telethon session size=%s bytes",
            len(session_bytes),
        )

        session_file.write_bytes(session_bytes)

        logger.info("Telethon session restored from environment.")

    except Exception as error:
        raise TelegramStorageError(
            f"Failed to restore Telethon session ({type(error).__name__})."
        ) from None


async def find_storage_channel(
    client: TelegramClient,
    channel_id: int,
) -> Channel | None:
    async for dialog in client.iter_dialogs():
        entity = dialog.entity

        if isinstance(entity, Channel) and channel_id in {
            entity.id,
            utils.get_peer_id(entity),
        }:
            return entity

    return None


class TelegramChannelStorage:
    def __init__(self) -> None:
        self.client: TelegramClient | None = None
        self.channel: Channel | None = None

    async def connect(self) -> None:
        logging.getLogger("telethon").disabled = True

        if not API_ID or not API_HASH or not CHANNEL_ID:
            raise TelegramStorageError(
                "Set API_ID, API_HASH, and CHANNEL_ID in .env."
            )

        try:
            api_id = int(API_ID)
            channel_id = int(CHANNEL_ID)
        except ValueError:
            raise TelegramStorageError(
                "API_ID and CHANNEL_ID in .env must be integers."
            ) from None

        if api_id <= 0:
            raise TelegramStorageError(
                "API_ID in .env must be positive."
            )

        # Restore session from Koyeb environment variable if available.
        restore_session_from_environment()

        SESSION_DIR.mkdir(parents=True, exist_ok=True)

        client = TelegramClient(
            str(SESSION_PATH),
            api_id,
            API_HASH,
            # Keep the connection stable for hosted environments.
            connection_retries=5,
            retry_delay=2,
            auto_reconnect=True,
        )

        try:
            await client.connect()

            if not await client.is_user_authorized():
                raise TelegramStorageError(
                    "The saved Telethon session is not authorized. "
                    "Check TELEGRAM_SESSION_BASE64."
                )

            channel = await find_storage_channel(
                client,
                channel_id,
            )

            if channel is None:
                raise TelegramStorageError(
                    "The configured channel is not available "
                    "to this Telegram account."
                )

        except TelegramStorageError:
            await client.disconnect()
            raise

        except Exception as error:
            await client.disconnect()

            raise TelegramStorageError(
                f"Telethon connection failed ({type(error).__name__})."
            ) from None

        self.client = client
        self.channel = channel

    async def upload_file(
        self,
        file_path: Path,
        filename: str,
    ) -> int:
        """
        Upload a local file to the Telegram storage channel.

        Uses Telethon's file upload directly from disk.
        The file is not loaded completely into RAM.
        """

        if self.client is None or self.channel is None:
            raise TelegramStorageError(
                "Telethon storage is not connected."
            )

        if not file_path.exists():
            raise TelegramStorageError(
                f"File not found: {file_path}"
            )

        file_size = file_path.stat().st_size

        if file_size <= 0:
            raise TelegramStorageError(
                "Cannot upload an empty file."
            )

        start_time = time.monotonic()
        last_log_time = start_time

        logger.info(
            "Upload start: filename=%s path=%s size_bytes=%s",
            filename,
            str(file_path),
            file_size,
        )

        def progress_callback(
            sent_bytes: int,
            total_bytes: int,
        ) -> None:
            nonlocal last_log_time

            now = time.monotonic()
            elapsed = max(
                now - start_time,
                0.1,
            )

            percent = (
                sent_bytes / total_bytes * 100.0
                if total_bytes
                else 0.0
            )

            speed = (
                sent_bytes / elapsed
                if elapsed > 0
                else 0.0
            )

            remaining = max(
                total_bytes - sent_bytes,
                0,
            )

            eta = (
                remaining / speed
                if speed > 0
                else 0.0
            )

            # Log every 10 seconds instead of every 5 seconds.
            # This keeps logging overhead very low.
            if (
                now - last_log_time >= 10
                or sent_bytes >= total_bytes
            ):
                logger.info(
                    "Upload progress: filename=%s "
                    "sent=%s total=%s percent=%.2f "
                    "speed_MB_per_s=%.2f eta_seconds=%.1f",
                    filename,
                    sent_bytes,
                    total_bytes,
                    percent,
                    speed / (1024 * 1024),
                    eta,
                )

                last_log_time = now

        try:
            # Direct upload from disk.
            #
            # Telethon handles the MTProto upload internally.
            # Do not read the complete file into memory.
            message = await self.client.send_file(
                self.channel,
                file_path,
                caption=filename,
                force_document=True,
                progress_callback=progress_callback,
                attributes=[
                    DocumentAttributeFilename(filename)
                ],
            )

        except Exception as error:
            logger.exception(
                "Telethon upload failed: filename=%s "
                "path=%s size_bytes=%s",
                filename,
                str(file_path),
                file_size,
            )

            raise TelegramStorageError(
                f"Telethon upload failed ({type(error).__name__})."
            ) from None

        if isinstance(message, list):
            if not message:
                raise TelegramStorageError(
                    "Telethon returned no uploaded message."
                )

            message = message[0]

        total_elapsed = time.monotonic() - start_time

        average_speed = (
            file_size / total_elapsed
            if total_elapsed > 0
            else 0.0
        )

        logger.info(
            "Upload success: filename=%s message_id=%s "
            "elapsed_seconds=%.2f average_MB_per_s=%.2f",
            filename,
            message.id,
            total_elapsed,
            average_speed / (1024 * 1024),
        )

        return message.id

    async def search_files(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if self.client is None or self.channel is None:
            raise TelegramStorageError(
                "Telethon storage is not connected."
            )

        normalized_query = query.strip()

        if not normalized_query:
            return []

        matches: list[dict[str, Any]] = []

        max_results = max(
            1,
            int(limit),
        )

        needle = normalized_query.lower()

        async for message in self.client.iter_messages(
            self.channel,
            limit=max_results * 20,
        ):
            if (
                message is None
                or getattr(message, "file", None) is None
            ):
                continue

            filename = ""

            if getattr(message.file, "name", None):
                filename = str(message.file.name)

            elif getattr(message, "document", None) is not None:
                for attribute in getattr(
                    message.document,
                    "attributes",
                    [],
                ):
                    if isinstance(
                        attribute,
                        DocumentAttributeFilename,
                    ):
                        filename = getattr(
                            attribute,
                            "file_name",
                            "",
                        )
                        break

            caption = (
                getattr(message, "caption", "")
                or getattr(message, "text", "")
                or ""
            ).strip()

            haystack = " ".join(
                part
                for part in (
                    filename,
                    caption,
                )
                if part
            )

            if not haystack:
                continue

            if needle in haystack.lower():
                matches.append(
                    {
                        "message_id": message.id,
                        "filename": (
                            filename
                            or caption
                            or "stored_file"
                        ),
                        "caption": caption,
                    }
                )

                if len(matches) >= max_results:
                    break

        return matches

    async def get_message_by_id(
        self,
        message_id: int,
    ) -> TelethonMessage | None:
        if self.client is None or self.channel is None:
            raise TelegramStorageError(
                "Telethon storage is not connected."
            )

        try:
            message = await self.client.get_messages(
                self.channel,
                ids=[message_id],
            )

        except Exception as error:
            raise TelegramStorageError(
                "Telethon message lookup failed "
                f"({type(error).__name__})."
            ) from None

        if isinstance(message, list):
            return message[0] if message else None

        return message

    async def download_message_to_path(
        self,
        message: TelethonMessage,
        file_path: Path,
    ) -> Path | None:
        if self.client is None:
            raise TelegramStorageError(
                "Telethon storage is not connected."
            )

        if getattr(message, "file", None) is None:
            return None

        try:
            downloaded_path = await self.client.download_media(
                message,
                file=str(file_path),
            )

        except Exception as error:
            raise TelegramStorageError(
                "Telethon file download failed "
                f"({type(error).__name__})."
            ) from None

        return (
            Path(downloaded_path)
            if isinstance(downloaded_path, str)
            else None
        )

    async def close(self) -> None:
        if (
            self.client is not None
            and self.client.is_connected()
        ):
            await self.client.disconnect()