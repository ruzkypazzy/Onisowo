"""
Oniṣòwò — main entry point.

Run this to start the bot:
    python main.py

Reads from .env (via python-dotenv) or from the actual environment.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("onisowo")


def main():
    """Start the Oniṣòwò Telegram bot."""
    # Verify env vars
    required = ["TELEGRAM_BOT_TOKEN", "BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE", "BITGET_QWEN_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"❌ Missing env vars: {', '.join(missing)}")
        print(f"   Edit your .env file and try again.")
        print(f"   See .env.example for reference.")
        sys.exit(1)

    # Print a friendly startup banner
    print()
    print("=" * 60)
    print("  Ọniṣọwọ́ (Oniṣòwò) — Yoruba AI Trading Agent")
    print("=" * 60)
    print()
    print(f"  Bitget:    {'✓' if os.environ.get('BITGET_API_KEY') else '✗'}")
    print(f"  Qwen:      {'✓' if os.environ.get('BITGET_QWEN_API_KEY') else '✗'}")
    print(f"  Telegram:  {'✓' if os.environ.get('TELEGRAM_BOT_TOKEN') else '✗'}")
    print(f"  Database:  {os.environ.get('DATABASE_PATH', './db/onisowo.db')}")
    print(f"  Max trade: ${os.environ.get('MAX_TRADE_USD', '2.00')}")
    print(f"  Max DD:    {float(os.environ.get('MAX_DRAWDOWN_PCT', '0.30'))*100:.0f}%")
    print()
    print("  Starting Telegram bot... (Ctrl+C to stop)")
    print()

    # Run the bot
    from telegram.bot import run_bot
    run_bot()


if __name__ == "__main__":
    main()
