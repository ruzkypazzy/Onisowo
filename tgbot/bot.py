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
    "*/buy SYMBOL USDT* — buy $X of SYMBOL (with advisor)\n"
    "*/sell SYMBOL USDT* — sell $X of SYMBOL (with advisor)\n"
    "*/force_buy* / */force_sell* — override a held advisory\n"
    "*/abort* — cancel a held advisory\n\n"
    "*/journal* — recent trade journal\n"
    "*/review* — 7-day review\n"
    "*/reflect* — recursive self-improvement\n"
    "*/memory* — show memory\n"
    "*/llm* — which LLM is powering me\n"
    "*/llms* — supported LLM providers\n"
    "*/time* — current WAT time + Yoruba greeting\n\n"
    "*/risk* — risk engine state\n"
    "*/kill REASON* — activate kill switch\n"
    "*/release* — release kill switch\n"
    "*/settings* — adjust limits\n\n"
    "🤖 *Autonomous:*\n"
    "*/strategist start|stop|status|tick* — autonomous trading loop\n"
    "*/strategy* — show strategy rules (watchlist, TP/SL, auto modes)\n"
    "*/positions* — open positions with adaptive TP/SL signals\n\n"
    "🎯 *3 trading modes:*\n"
    "• *Manual:* `/buy SYMBOL USDT` — you pick, bot executes\n"
    "• *Semi:* `/analyze SYMBOL USDT` → `/proceed` — bot analyzes, you confirm\n"
    "• *Autonomous:* `/autotrade USDT` — bot scans, picks, executes (with safety net)\n"
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

    if cmd in ("force_buy", "force_sell"):
        # /force_buy SOL 100 — uses the cached pending advisory's trade,
        # args are optional (bot can take them from the pending cache).
        m = re.match(r"(\S+)\s+(\d+(?:\.\d+)?)", rest)
        if m:
            return (cmd, {"symbol": m.group(1).upper(), "amount_usd": float(m.group(2))})
        return (cmd, {})

    if cmd == "price":
        return (cmd, {"symbol": rest.strip().upper() or "BTCUSDT"})

    if cmd == "kill":
        return (cmd, {"extra": rest.split() if rest else []})

    if cmd == "skill":
        parts2 = rest.split(maxsplit=1)
        return (cmd, {"name": parts2[0] if parts2 else "", "extra": parts2[1].split() if len(parts2) > 1 else []})

    if cmd in ("start", "help", "about", "status", "balance", "skills", "journal",
               "review", "reflect", "memory", "risk", "release", "settings", "pnl",
               "llm", "llms", "time", "abort", "strategy", "positions",
               "analyze", "autotrade", "proceed"):
        return (cmd, {})

    if cmd == "strategist":
        # /strategist [start|stop|status|tick] — the agent unpacks the subcommand from user_message
        return (cmd, {"sub": rest.strip().lower().split()[0] if rest.strip() else ""})

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

    # Register the command list with Telegram so they show as clickable chips
    # in the chat UI (instead of having to type /command manually).
    async def _post_init(app):
        from telegram import BotCommand
        commands = [
            BotCommand("start", "Ọniṣọwọ́ greeting"),
            BotCommand("help", "all commands"),
            BotCommand("about", "about this bot"),
            BotCommand("status", "portfolio + balance"),
            BotCommand("balance", "USDT balance only"),
            BotCommand("price", "current price of a symbol"),
            BotCommand("buy", "buy $X of SYMBOL (with advisor)"),
            BotCommand("sell", "sell $X of SYMBOL (with advisor)"),
            BotCommand("analyze", "deep analysis + bot's TP/SL (semi-autonomous)"),
            BotCommand("proceed", "execute the pending analysis"),
            BotCommand("abort", "cancel a pending advisory or analysis"),
            BotCommand("autotrade", "autonomous mode: scan + pick + execute"),
            BotCommand("strategist", "start/stop/status/tick the autonomous loop"),
            BotCommand("strategy", "show strategy rules"),
            BotCommand("positions", "open positions with adaptive TP/SL"),
            BotCommand("skills", "list all 132 skills"),
            BotCommand("skill", "invoke a specific skill"),
            BotCommand("journal", "recent trade journal"),
            BotCommand("review", "7-day review"),
            BotCommand("reflect", "recursive self-improvement"),
            BotCommand("memory", "show memory"),
            BotCommand("llm", "which LLM is powering me"),
            BotCommand("llms", "supported LLM providers"),
            BotCommand("time", "WAT time + Yoruba greeting"),
            BotCommand("risk", "risk engine state"),
            BotCommand("kill", "activate kill switch"),
            BotCommand("release", "release kill switch"),
            BotCommand("settings", "adjust limits"),
        ]
        await app.bot.set_my_commands(commands)
        logger.info("Telegram bot commands registered (clickable in chat UI)")

    app.post_init = _post_init

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
