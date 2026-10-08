import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telethon_storage import TelegramChannelStorage


class DummyFile:
    def __init__(self, name):
        self.name = name


class DummyMessage:
    def __init__(self, file_name, caption=None, message_id=1):
        self.id = message_id
        self.file = DummyFile(file_name) if file_name else None
        self.caption = caption
        self.text = None


class TelegramStorageSearchTests(unittest.TestCase):
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

    def test_download_message_to_path_uses_local_destination(self):
        async def download_media(message, file):
            Path(file).write_bytes(b"test file")
            return file

        storage = TelegramChannelStorage()
        storage.client = SimpleNamespace(download_media=download_media)

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "stored_file"
            downloaded_path = asyncio.run(
                storage.download_message_to_path(
                    DummyMessage("report.pdf"),
                    destination,
                )
            )

            self.assertEqual(downloaded_path, destination)
            self.assertEqual(destination.read_bytes(), b"test file")

    def _message_stream(self, messages):
        async def iterator():
            for message in messages:
                yield message

        return iterator()


if __name__ == "__main__":
    unittest.main()
