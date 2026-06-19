"""
Set the bot's Telegram profile picture from assets/akanji_photo.jpg.

Run on the VPS after the user has uploaded the photo (either via
/upload_photo <url>, or by sending the photo to the bot).

Usage:
  python tools/set_bot_photo.py
"""
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PHOTO_PATH = REPO_ROOT / "assets" / "akanji_photo.jpg"


async def main():
    if not PHOTO_PATH.exists():
        print(f"❌ Photo not found at {PHOTO_PATH}")
        print("Upload it first via /upload_photo <url> or send it to the bot.")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    from telegram import Bot
    bot = Bot(token=token)
    with open(PHOTO_PATH, "rb") as f:
        ok = await bot.set_my_profile_photo(photo=f)
    if ok:
        print(f"✓ Bot profile picture set from {PHOTO_PATH}")
    else:
        print("⚠️  Telegram returned False (photo may be invalid or too large)")
        print("    Telegram profile photos must be:")
        print("    - JPEG format")
        print("    - ≤ 10 MB")
        print("    - Square or close to square (1:1 aspect ratio)")
        print("    - At least 320×320 pixels")


if __name__ == "__main__":
    asyncio.run(main())
