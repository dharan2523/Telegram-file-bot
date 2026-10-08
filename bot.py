import asyncio
import logging
import tempfile
import traceback
from pathlib import Path

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import BOT_TOKEN, DOWNLOAD_DIR, MAX_STORAGE_FILE_BYTES, MAX_UPLOAD_BYTES, UPLOAD_TIMEOUT
from downloader import DownloadTooLargeError, download_file, validate_http_url
from telethon_storage import TelegramChannelStorage, TelegramStorageError

logger = logging.getLogger(__name__)
class _CredentialRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not BOT_TOKEN:
            return True

        message = record.getMessage()
        if BOT_TOKEN in message:
            record.msg = message.replace(BOT_TOKEN, "[REDACTED]")
            record.args = ()

        if record.exc_info:
            exception_text = "".join(traceback.format_exception(*record.exc_info))
            if BOT_TOKEN in exception_text:
                record.exc_info = None
                record.exc_text = exception_text.replace(BOT_TOKEN, "[REDACTED]")

        return True


logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Send a direct HTTP/HTTPS file link. I will store it in the private channel "
            "and send it back if it is within Telegram Cloud's 50 MB limit. "
            "You can also use /search <filename> to find files already stored there."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Send a direct HTTP/HTTPS file link. The file will be stored in the private "
            "Telegram channel. Files up to 50 MB can also be sent back to you; larger files "
            "can only be stored in the channel. Use /search <filename> to look for previously "
            "uploaded files in the storage channel."
        )


async def search_storage(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    message = update.message
    if not message:
        return

    storage: TelegramChannelStorage | None = context.application.bot_data.get("storage")
    if storage is None:
        await message.reply_text("Storage is not ready yet. Try again in a moment.")
        return

    try:
        matches = await storage.search_files(query, limit=10)
    except TelegramStorageError as error:
        logger.error("Storage search failed (%s)", type(error).__name__)
        await message.reply_text("⚠️ Search is unavailable right now.")
        return

    if not matches:
        await message.reply_text(f"No files matching '{query}' were found in the storage channel.")
        return

    keyboard = [[
        InlineKeyboardButton(
            text=(item["filename"][:32] + "...") if len(str(item["filename"])) > 32 else str(item["filename"]),
            callback_data=f"file:{item['message_id']}",
        )
    ] for item in matches]
    await message.reply_text(
        f"Found {len(matches)} matching file(s):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    if not context.args:
        if context.user_data is None:
            await message.reply_text("Search is unavailable right now. Please try /search <filename>.")
            return
        context.user_data["search_mode"] = True
        await message.reply_text("Send a filename or part of a filename to search the storage channel.")
        return

    query = " ".join(context.args)
    await search_storage(update, context, query)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    text = message.text.strip()
    if text.startswith("/"):
        return

    if context.user_data is not None and context.user_data.get("search_mode"):
        context.user_data["search_mode"] = False
        await search_storage(update, context, text)
        return

    try:
        validate_http_url(text)
    except ValueError:
        await message.reply_text(
            "Please send a valid direct HTTP/HTTPS file link, or use /search <filename> to find a file in the storage channel."
        )
        return

    await handle_url(update, context)


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    url = message.text.strip()
    try:
        validate_http_url(url)
    except ValueError:
        await message.reply_text("Please send a valid direct HTTP/HTTPS file link.")
        return

    await message.reply_text("🔗 URL received")
    await message.reply_text("⬇️ Downloading...")

    downloaded_file = None
    upload_completed = False
    try:
        downloaded_file = await asyncio.to_thread(download_file, url)
        if downloaded_file.size > MAX_STORAGE_FILE_BYTES:
            raise DownloadTooLargeError

        await message.reply_text("📤 Uploading to storage...")
        storage: TelegramChannelStorage = context.application.bot_data["storage"]
        message_id = await storage.upload_file(
            downloaded_file.path,
            downloaded_file.filename,
        )
        upload_completed = True
        logger.info("Stored file in channel; message_id=%s", message_id)
        if downloaded_file.size > MAX_UPLOAD_BYTES:
            await message.reply_text(
                "✅ File stored in the private channel. It is too large to send back "
                "through Telegram Cloud Bot API (50 MB maximum)."
            )
            return

        await message.reply_text("📨 Sending file to you...")
        try:
            await message.reply_document(
                document=downloaded_file.path,
                filename=downloaded_file.filename,
                connect_timeout=UPLOAD_TIMEOUT["connect_timeout"],
                read_timeout=UPLOAD_TIMEOUT["read_timeout"],
                write_timeout=UPLOAD_TIMEOUT["write_timeout"],
                pool_timeout=UPLOAD_TIMEOUT["pool_timeout"],
            )
        except TelegramError as error:
            logger.error("Stored file, but user delivery failed (%s)", type(error).__name__)
            await message.reply_text(
                "✅ File is stored in the private channel, but Telegram could not send it to you."
            )
        else:
            await message.reply_text("✅ File stored and sent to you.")
    except DownloadTooLargeError:
        logger.error("Download rejected because the file exceeds the 2 GiB limit for this bot.")
        await message.reply_text(
            "❌ The file exceeds the 2 GiB application storage limit."
        )
    except (TelegramError, TelegramStorageError) as error:
        logger.error("File storage failed (%s)", type(error).__name__)
        await message.reply_text("❌ Unable to store the file. Please check the link and try again.")
    except requests.HTTPError as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        logger.error("Direct URL download failed with HTTP status %s", status)
        await message.reply_text("❌ The file could not be downloaded from that URL.")
    except Exception as error:
        logger.error("File processing failed (%s)", type(error).__name__)
        await message.reply_text(
            "❌ Unable to download or store the file. Please check the link and try again."
        )
    finally:
        if downloaded_file is not None:
            try:
                if upload_completed or downloaded_file.size > MAX_STORAGE_FILE_BYTES:
                    downloaded_file.path.unlink(missing_ok=True)
                else:
                    downloaded_file.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove temporary file %s", downloaded_file.path)


async def handle_file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data or not query.data.startswith("file:"):
        return

    await query.answer("Sending file...")

    message_id_text = query.data.split(":", 1)[1]
    try:
        message_id = int(message_id_text)
    except ValueError:
        await query.edit_message_text("⚠️ Invalid file result.")
        return

    storage: TelegramChannelStorage | None = context.application.bot_data.get("storage")
    if storage is None:
        await query.edit_message_text("⚠️ Storage is not available right now.")
        return

    try:
        message = await storage.get_message_by_id(message_id)
    except TelegramStorageError as error:
        logger.error("Message lookup failed (%s)", type(error).__name__)
        await query.edit_message_text("⚠️ Unable to retrieve that file.")
        return

    if message is None:
        await query.edit_message_text("⚠️ This file is no longer available.")
        return

    stored_file = getattr(message, "file", None)
    if stored_file is None:
        await query.edit_message_text("⚠️ This file is no longer available.")
        return

    filename = (
        getattr(stored_file, "name", None)
        or getattr(message, "text", None)
        or "stored_file"
    )
    file_size = getattr(stored_file, "size", None)
    if isinstance(file_size, int) and file_size > MAX_UPLOAD_BYTES:
        await query.edit_message_text(
            "⚠️ This file is stored, but exceeds Telegram Cloud Bot API's 50 MB send limit."
        )
        return

    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="storage_", dir=DOWNLOAD_DIR) as temp_dir:
            file_path = Path(temp_dir) / "stored_file"
            downloaded_path = await storage.download_message_to_path(message, file_path)
            if downloaded_path is None:
                await query.edit_message_text("⚠️ This file is no longer available.")
                return
            if downloaded_path.stat().st_size > MAX_UPLOAD_BYTES:
                await query.edit_message_text(
                    "⚠️ This file is stored, but exceeds Telegram Cloud Bot API's 50 MB send limit."
                )
                return
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=downloaded_path,
                filename=filename,
            )
        await query.edit_message_text("✅ File sent.")
    except TelegramStorageError as error:
        logger.error("Stored-file download failed (%s)", type(error).__name__)
        await query.edit_message_text("⚠️ Unable to download that file from storage.")
    except TelegramError as error:
        logger.error("Stored-file send failed (%s)", type(error).__name__)
        await query.edit_message_text("⚠️ Telegram could not send that file to you.")


async def initialize_storage(application: Application) -> None:
    storage = TelegramChannelStorage()
    await storage.connect()
    application.bot_data["storage"] = storage
    logger.info("Telethon storage channel is ready.")


async def shutdown_storage(application: Application) -> None:
    storage: TelegramChannelStorage | None = application.bot_data.get("storage")
    if storage is not None:
        await storage.close()


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_CredentialRedactionFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in your .env file or environment.")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(initialize_storage)
        .post_shutdown(shutdown_storage)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_handler(CallbackQueryHandler(handle_file_callback, pattern="^file:"))

    logger.info("Starting Telegram file bot")
    application.run_polling()


if __name__ == "__main__":
    main()