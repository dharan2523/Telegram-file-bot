import asyncio
import logging

from config import API_HASH, API_ID, CHANNEL_ID
from telethon_storage import SESSION_DIR, SESSION_PATH, find_storage_channel


def required_credentials() -> tuple[int, str, int]:
    if not API_ID or not API_HASH or not CHANNEL_ID:
        raise ValueError("Set API_ID, API_HASH, and CHANNEL_ID in .env")

    try:
        api_id = int(API_ID)
        channel_id = int(CHANNEL_ID)
    except ValueError as error:
        raise ValueError("API_ID and CHANNEL_ID must be integers") from error

    if api_id <= 0:
        raise ValueError("API_ID must be a positive integer")

    return api_id, API_HASH, channel_id


async def main() -> int:
    logging.getLogger("telethon").disabled = True

    try:
        api_id, api_hash, channel_id = required_credentials()
    except ValueError as error:
        print(f"Configuration error: {error}")
        return 1

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION_PATH), api_id, api_hash)

    try:
        await client.start()
        if not await client.is_user_authorized():
            print("Login failed: Telegram user authorization was not completed.")
            return 1

        print("Telegram account connection: OK")
        channel = await find_storage_channel(client, channel_id)
        if channel is None:
            print("Channel access: NOT FOUND")
            print("Join the private channel with this Telegram account and check CHANNEL_ID.")
            return 1

        print(f"Channel access: OK ({channel.title})")
        print(f"Session file: {SESSION_PATH.with_suffix('.session')}")
        return 0
    except Exception as error:
        print(f"Connection test failed ({type(error).__name__}).")
        return 1
    finally:
        await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))