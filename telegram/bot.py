"""
Telegram bot surface — the Oniṣòwò user interface.

Uses python-telegram-bot v21+. Handles:
- /start, /help, /about, /skills, /skill, /status, /balance, /price
- /buy SYMBOL AMOUNT, /sell SYMBOL AMOUNT
- /journal, /pnl, /risk, /kill, /release, /settings
- /memory, /reflect, /review

Free-form text → routed through the agent brain (Qwen).
"""

import os
import re
import logging
import asyncio
from typing import Optional
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

from agent.core import Agent, AgentContext

logger = logging.getLogger(__name__)


HELP_TEXT = (
    "*Oniṣòwò commands:*\n\n"
    "*/start* — greeting\n"
    "*/help* — this list\n"
    "*/about* — about this bot\n"
    "*/skills* — list all 100+ skills\n"
    "*/skill NAME* — invoke a specific skill\n\n"
    "*/status* — portfolio + balance\n"
    "*/balance* — USDT balance only\n"
    "*/price SYMBOL* — current price\n"
    "*/buy SYMBOL USDT* — buy $X of SYMBOL\n"
    "*/sell SYMBOL USDT* — sell $X of SYMBOL\n\n"
    "*/journal* — recent trade journal\n"
    "*/review* — 7-day review\n"
    "*/reflect* — recursive self-improvement\n"
    "*/memory* — show memory\n\n"
    "*/risk* — risk engine state\n"
    "*/kill REASON* — activate kill switch\n"
    "*/release* — release kill switch\n"
    "*/settings* — adjust limits\n"
)


def parse_command_args(text: str) -> tuple[str, dict]:
    """Parse a command like '/buy SOL 100' into ('buy', {'symbol': 'SOL', 'amount_usd': 100})."""
    text = text.strip()
    if not text.startswith("/"):
        return ("ask", {"text": text})

    # Strip / and split
    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]  # handle /cmd@botname
    rest = parts[1] if len(parts) > 1 else ""

    # Parse known commands with args
    if cmd in ("buy", "sell"):
        # /buy SOL 100 or /buy SOLUSDT 100
        m = re.match(r"(\S+)\s+(\d+(?:\.\d+)?)", rest)
        if m:
            symbol = m.group(1).upper()
            amount = float(m.group(2))
            return (cmd, {"symbol": symbol, "amount_usd": amount})
        return (cmd, {"symbol": rest.strip(), "amount_usd": 0, "extra": rest.split()})

    if cmd == "price":
        return (cmd, {"symbol": rest.strip().upper() or "BTCUSDT"})

    if cmd == "kill":
        return (cmd, {"extra": rest.split() if rest else []})

    if cmd == "skill":
        parts2 = rest.split(maxsplit=1)
        return (cmd, {"name": parts2[0] if parts2 else "", "extra": parts2[1].split() if len(parts2) > 1 else []})

    if cmd in ("start", "help", "about", "status", "balance", "skills", "journal",
               "review", "reflect", "memory", "risk", "release", "settings", "pnl"):
        return (cmd, {})

    return ("ask", {"text": text})


def run_bot(token: Optional[str] = None):
    """Run the Telegram bot (blocking)."""

    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN missing. Set it in your .env or pass to run_bot()."
        )

    # Initialize the agent
    agent = Agent()

    # Build the application
    app = Application.builder().token(token).build()

    # -------------------------------------------------------------------------
    # Handlers
    # -------------------------------------------------------------------------

    async def cmd_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle a slash command."""
        if not update.message or not update.message.text:
            return
        text = update.message.text
        cmd, args = parse_command_args(text)

        ctx = AgentContext(
            user_id=update.effective_user.id if update.effective_user else 0,
            user_message=text,
            command=cmd,
            args=args,
        )

        # Run the agent in a thread to avoid blocking the event loop
        try:
            response = await asyncio.to_thread(agent.handle, ctx)
        except Exception as e:
            logger.exception(f"Agent error: {e}")
            response = f"❌ Error: {e}"

        # Send response (split if too long — Telegram 4096 char limit)
        for chunk in _split_message(response):
            await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

    async def freeform_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle free-form text (not a slash command)."""
        if not update.message or not update.message.text:
            return
        text = update.message.text
        ctx = AgentContext(
            user_id=update.effective_user.id if update.effective_user else 0,
            user_message=text,
            command="ask",
            args={"text": text},
        )
        try:
            response = await asyncio.to_thread(agent.handle, ctx)
        except Exception as e:
            logger.exception(f"Agent error: {e}")
            response = f"❌ Error: {e}"

        for chunk in _split_message(response):
            await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Telegram bot error: {context.error}")

    # Register handlers
    app.add_handler(MessageHandler(filters.COMMAND, cmd_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, freeform_handler))
    app.add_error_handler(error_handler)

    # Run
    logger.info("Ọniṣọwọ́ Telegram bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find a good split point (newline near max_len)
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
