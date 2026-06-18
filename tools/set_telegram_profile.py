"""
Set the Telegram bot's profile picture and description to Àkànjí Oníṣòwò.

Run once after install:
    python tools/set_telegram_profile.py

Requires the bundled assets/profile_picture.png and TELEGRAM_BOT_TOKEN in .env.
Uploads the profile pic, sets the bot's name, and the about text.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN not set in .env")
    sys.exit(1)

PROFILE_PIC = Path(__file__).parent.parent / "assets" / "profile_picture.png"
if not PROFILE_PIC.exists():
    print(f"❌ Profile picture not found: {PROFILE_PIC}")
    sys.exit(1)

BOT_NAME = "Àkànjí Oníṣòwò"
BOT_ABOUT = (
    "Àkànjí — The Trader. AI agent rooted in Yoruba merchant wisdom, "
    "now trading crypto on Bitget. Self-hostable, open-source, MEV-aware, "
    "sybil-scored, recursive self-improvement. Built for the Bitget AI Base Camp."
)
BOT_DESCRIPTION = (
    "Ọniṣọwọ́ — Yoruba for 'trader'. I'm Àkànjí, an AI trading agent.\n\n"
    "What I do:\n"
    "• Trade crypto on Bitget (spot, 100+ skills)\n"
    "• Analyze markets, manage risk, execute strategies\n"
    "• Learn from every trade (memory + journaling)\n"
    "• Run autonomously or take your commands\n\n"
    "How to use me:\n"
    "/start — meet Àkànjí\n"
    "/analyze SYMBOL USDT — deep-dive a pair\n"
    "/buy SYMBOL USDT — manual buy\n"
    "/autotrade USDT — let me pick + trade\n"
    "/strategist start — run me on autopilot\n"
    "/journal — review past trades\n"
    "/help — full command list\n\n"
    "Powered by Qwen 3.6 Plus. Yoruba time-of-day greetings. "
    "Self-hostable at github.com/ruzkypazzy/Onisowo"
)


def upload_profile_picture(bot):
    """Upload the bundled profile picture to Telegram."""
    print(f"→ Uploading {PROFILE_PIC.name} ({PROFILE_PIC.stat().st_size} bytes)...")
    with open(PROFILE_PIC, "rb") as f:
        # python-telegram-bot 20.x: set_bot_photo
        result = bot.set_bot_photo(photo=f.read())
    if result:
        print("  ✓ Profile picture set")
    else:
        print("  ⚠️  set_bot_photo returned False (may already be set)")
    return result


def set_name(bot, name: str = BOT_NAME):
    """Set the bot's display name."""
    print(f"→ Setting bot name: {name}")
    result = bot.set_my_name(name=name)
    print(f"  ✓ Name set: {result.name if result else 'OK'}")
    return result


def set_about(bot, about: str = BOT_ABOUT):
    """Set the short 'about' blurb."""
    print(f"→ Setting bot about ({len(about)} chars)...")
    result = bot.set_my_short_description(short_description=about)
    print(f"  ✓ About set")
    return result


def set_description(bot, desc: str = BOT_DESCRIPTION):
    """Set the long description (visible in profile)."""
    print(f"→ Setting bot description ({len(desc)} chars)...")
    result = bot.set_my_description(description=desc)
    print(f"  ✓ Description set")
    return result


def main():
    try:
        from telegram import Bot
    except ImportError:
        print("❌ python-telegram-bot not installed. pip install python-telegram-bot")
        sys.exit(1)

    bot = Bot(token=TOKEN)
    me = bot.get_me()
    print(f"  Bot: @{me.username} ({me.first_name})")
    print()

    try:
        set_name(bot)
        set_about(bot)
        set_description(bot)
        upload_profile_picture(bot)
    except Exception as e:
        print(f"❌ Failed: {e}")
        print()
        print("Note: setting the bot's profile picture requires uploading an image.")
        print("Telegram may rate-limit or reject this if the bot is too new or the")
        print("image doesn't meet the 512x512 minimum requirement.")
        sys.exit(1)

    print()
    print("✓ Telegram profile set up for Àkànjí Oníṣòwò")
    print()
    print("Verify by opening @{} in Telegram and checking the profile photo + bio.".format(me.username))


if __name__ == "__main__":
    main()
