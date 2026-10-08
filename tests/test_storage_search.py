import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import handle_file_callback
from config import (
    TELETHON_UPLOAD_PART_SIZE_KB,
    _parse_telethon_upload_part_size_kb,
)
from telethon_storage import TelegramChannelStorage, TelegramStorageError


class DummyFile:
    def __init__(self, name, size=0):
        self.name = name
        self.size = size


class DummyMessage:
    def __init__(self, file_name, caption=None, message_id=1, size=0):
        self.id = message_id
        self.file = DummyFile(file_name, size) if file_name else None
        self.caption = caption
        self.text = None


class TelegramStorageSearchTests(unittest.TestCase):
    def test_upload_part_size_setting_defaults_and_validates(self):
        self.assertEqual(_parse_telethon_upload_part_size_kb(None), 512)
        self.assertEqual(_parse_telethon_upload_part_size_kb("256"), 256)

        with self.assertRaises(ValueError):
            _parse_telethon_upload_part_size_kb("1024")

    def test_upload_uses_configured_part_size_and_preserves_filename(self):
        uploaded_handle = object()
        upload_calls = []
        send_calls = []

        async def upload_file(
            file_path,
            *,
            part_size_kb,
            file_size,
            file_name,
            progress_callback,
        ):
            upload_calls.append(
                {
                    "file_path": file_path,
                    "part_size_kb": part_size_kb,
                    "file_size": file_size,
                    "file_name": file_name,
                }
            )
            progress_callback(file_size, file_size)
            return uploaded_handle

        async def send_file(channel, file, **kwargs):
            send_calls.append((channel, file, kwargs))
            return SimpleNamespace(id=77)

        storage = TelegramChannelStorage()
        storage.client = SimpleNamespace(
            upload_file=upload_file,
            send_file=send_file,
        )
        storage.channel = object()
        filename = "authorized-test.bin"

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / filename
            file_path.write_bytes(b"test content")
            message_id = asyncio.run(
                storage.upload_file(file_path, filename)
            )

        self.assertEqual(message_id, 77)
        self.assertEqual(upload_calls[0]["file_path"], file_path)
        self.assertEqual(
            upload_calls[0]["part_size_kb"],
            TELETHON_UPLOAD_PART_SIZE_KB,
        )
        self.assertEqual(upload_calls[0]["file_size"], len(b"test content"))
        self.assertEqual(upload_calls[0]["file_name"], filename)
        self.assertIs(send_calls[0][1], uploaded_handle)
        self.assertEqual(send_calls[0][2]["caption"], filename)
        self.assertEqual(
            send_calls[0][2]["attributes"][0].file_name,
            filename,
        )
        self.assertNotIn("progress_callback", send_calls[0][2])

    def test_search_files_matches_document_name_and_caption(self):
        storage = TelegramChannelStorage()
        storage.client = SimpleNamespace(
            iter_messages=lambda channel, limit=None: self._message_stream([
                DummyMessage("Quarterly_Report.pdf", "Quarterly report"),
                DummyMessage("notes.txt", "Random text"),
            ])
        )
        storage.channel = object()

        results = asyncio.run(storage.search_files("report", limit=10))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "Quarterly_Report.pdf")
        self.assertEqual(results[0]["message_id"], 1)

    def test_delivery_resends_existing_message_through_telethon(self):
        sent_messages = []

        async def send_message(user_id, message):
            sent_messages.append((user_id, message))

        storage = TelegramChannelStorage()
        storage.client = SimpleNamespace(send_message=send_message)
        storage.channel = object()
        message = DummyMessage("report.pdf")

        asyncio.run(
            storage.send_stored_message_to_user(
                message,
                user_id=12345,
            )
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0][0], 12345)
        self.assertIs(sent_messages[0][1], message)

    def test_delivery_wraps_telethon_recipient_errors(self):
        async def send_message(user_id, message):
            raise ValueError("peer not cached")

        storage = TelegramChannelStorage()
        storage.client = SimpleNamespace(send_message=send_message)
        storage.channel = object()

        with self.assertRaisesRegex(
            TelegramStorageError,
            "Telethon could not deliver the stored file",
        ):
            asyncio.run(
                storage.send_stored_message_to_user(
                    DummyMessage("report.pdf"),
                    user_id=12345,
                )
            )

    def test_large_search_result_uses_telethon_not_bot_api(self):
        large_message = DummyMessage(
            "large-video.mkv",
            message_id=7,
            size=60 * 1024 * 1024,
        )
        telethon_deliveries = []

        async def get_message_by_id(message_id):
            return large_message

        async def send_stored_message_to_user(message, user_id):
            telethon_deliveries.append((message, user_id))

        storage = SimpleNamespace(
            get_message_by_id=get_message_by_id,
            send_stored_message_to_user=send_stored_message_to_user,
        )
        query = SimpleNamespace(
            data="file:7",
            from_user=SimpleNamespace(id=12345),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        bot = SimpleNamespace(send_document=AsyncMock())
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"storage": storage}),
            bot=bot,
        )
        update = SimpleNamespace(callback_query=query)

        asyncio.run(handle_file_callback(update, context))

        self.assertGreater(large_message.file.size, 50 * 1024 * 1024)
        self.assertEqual(telethon_deliveries, [(large_message, 12345)])
        bot.send_document.assert_not_awaited()
        query.edit_message_text.assert_awaited_with("✅ File sent.")

    def _message_stream(self, messages):
        async def iterator():
            for message in messages:
                yield message

        return iterator()


if __name__ == "__main__":
    unittest.main()
