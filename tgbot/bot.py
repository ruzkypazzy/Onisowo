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
import json
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
    "*Àkànjí Oníṣòwò — Àkànjí, The Trader*\n\n"
    "“Àkànjí Oníṣòwò” literally means *“Àkànjí is a trader.”* The bot carries his mind.\n"
    "Just send a message — I interpret and act.\n\n"
    "Just send a message. I interpret and act.\n\n"
    "*Try typing:*\n"
    "• `buy 2 SOL` — buy $2 of SOL\n"
    "• `sell my BTC` — sell whatever BTC you have\n"
    "• `long ETH with $5` — same as buy, in trader-speak\n"
    "• `analyze SOL` — deep analysis + my suggested TP/SL\n"
    "• `autotrade 2` — I scan the market, pick the best, execute\n"
    "• `what's the price of BTC?` — quick price check\n"
    "• `show my balance` / `my positions` — read-only queries\n"
    "• `start the strategist` — autonomous mode on a loop\n\n"
    "*Slash commands still work:*\n"
    "*/start* / */intro* / */help* / */about* / */status* / */balance*\n"
    "*/price* / */buy* / */sell* / */analyze* / */proceed*\n"
    "*/autotrade* / */strategist* / */strategy* / */positions*\n"
    "*/skills* / */skill* / */journal* / */review* / */reflect*\n"
    "*/memory* / */llm* / */llms* / */time* / */risk* / */abort*\n\n"
    "🤖 *3 trading modes:*\n"
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
               "analyze", "autotrade", "proceed", "intro", "control",
               "pick", "pickspot", "pickfuture", "daily", "history", "export", "showlog"):
        return (cmd, {})

    if cmd == "strategist":
        # /strategist [start|stop|status|tick] — the agent unpacks the subcommand from user_message
        return (cmd, {"sub": rest.strip().lower().split()[0] if rest.strip() else ""})

    if cmd == "settings":
        # /settings [key [value]] — sub + rest of the args
        parts2 = rest.split(maxsplit=1)
        sub = parts2[0].lower() if parts2 else ""
        rest2 = parts2[1].split() if len(parts2) > 1 else []
        return (cmd, {"sub": sub, "rest": rest2})

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
            BotCommand("start", "Àkànjí greeting"),
            BotCommand("intro", "read Àkànjí's origin story"),
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
            BotCommand("pick", "scan market, pick best trade (auto spot/futures by analysis)"),
            BotCommand("pickspot", "force spot trade (not futures)"),
            BotCommand("pickfuture", "futures trade with leverage"),
            BotCommand("daily", "alias for /pick — pick today's trade"),
            BotCommand("history", "detailed trade history with stats"),
            BotCommand("export", "export trade history to a text file"),
            BotCommand("strategist", "start/stop/status/tick the autonomous loop"),
            BotCommand("strategy", "show strategy rules"),
            BotCommand("positions", "open positions with adaptive TP/SL"),
            BotCommand("skills", "list all 186 skills"),
            BotCommand("skill", "invoke a specific skill"),
            BotCommand("journal", "recent trade journal"),
            BotCommand("review", "7-day review"),
            BotCommand("reflect", "recursive self-improvement"),
            BotCommand("memory", "show memory"),
            BotCommand("llm", "which LLM is powering me"),
            BotCommand("llms", "supported LLM providers"),
            BotCommand("time", "WAT time + Yoruba greeting"),
            BotCommand("control", "status/restart/stop/logs of the bot itself"),
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

    async def send_typing(update):
        """Send 'typing...' action repeatedly while the agent thinks."""
        from telegram.constants import ChatAction
        try:
            await update.message.chat.send_action(ChatAction.TYPING)
        except Exception:
            pass

    async def send_status(update, text: str):
        """Send a transient 'thinking' status message. Returns the message object so it can be edited."""
        try:
            msg = await update.message.reply_text(text)
            return msg
        except Exception as e:
            logger.exception(f"send_status failed: {e}")
            return None

    async def edit_status(msg, text: str):
        """Edit a previously-sent status message in place."""
        if msg is None:
            return
        try:
            await msg.edit_text(text)
        except Exception:
            pass

    async def cmd_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle a slash command."""
        if not update.message or not update.message.text:
            return
        text = update.message.text
        cmd, args = parse_command_args(text)

        # Show 'typing...' indicator + a status message immediately
        await send_typing(update)
        status_msg = await send_status(update, "🤔 Reading your request…")

        ctx = AgentContext(
            user_id=update.effective_user.id if update.effective_user else 0,
            user_message=text,
            command=cmd,
            args=args,
        )

        # Run the agent in a thread to avoid blocking the event loop.
        # During execution, update the status message with progress hints.
        import re as _re
        m = _re.match(r"^/(\w+)", text.strip())
        cmd_name = m.group(1) if m else "command"
        # Step-by-step status flow
        if cmd_name in ("buy", "sell"):
            await edit_status(status_msg, "📊 Fetching live price for " + str(args.get("symbol", "?")) + "…")
            await asyncio.sleep(0.4)
            await edit_status(status_msg, "🛡️ Running risk check…")
            await asyncio.sleep(0.4)
            await edit_status(status_msg, "🧠 Asking Qwen for reasoning…")
        elif cmd_name == "analyze":
            await edit_status(status_msg, "📡 Pulling market data (candles, RSI, MACD, ATR, ADX, S/R)…")
            await asyncio.sleep(0.4)
            await edit_status(status_msg, "🧮 Computing 9-signal composite score…")
            await asyncio.sleep(0.4)
            await edit_status(status_msg, "🤖 Asking Qwen for the trade thesis…")
        elif cmd_name == "autotrade":
            await edit_status(status_msg, "🔍 Scanning top 50 USDT pairs by volume…")
            await asyncio.sleep(0.4)
            await edit_status(status_msg, "📊 Scoring top 10 with 9-signal model…")
            await asyncio.sleep(0.4)
            await edit_status(status_msg, "🤖 Asking Qwen to pick the winner…")
        elif cmd_name == "strategist":
            await edit_status(status_msg, "🤖 Checking the autonomous loop status…")
        elif cmd_name in ("start", "help", "about", "skills"):
            pass  # no streaming needed for static pages
        else:
            await edit_status(status_msg, "🧠 Thinking…")
        try:
            response = await asyncio.to_thread(agent.handle, ctx)
        except Exception as e:
            logger.exception(f"Agent error: {e}")
            response = f"❌ Error: {e}"

        # Edit the status message with the final result (or delete it and send a fresh one for long replies)
        if response and len(response) <= 3800:
            await edit_status(status_msg, response)
        else:
            # Long reply — delete the status placeholder and send the real answer
            try:
                await status_msg.delete()
            except Exception:
                pass
            for chunk in _split_message(response):
                await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

    async def freeform_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle free-form text (not a slash command)."""
        if not update.message or not update.message.text:
            return
        text = update.message.text
        await send_typing(update)
        status_msg = await send_status(update, "🤔 Reading your prompt…")
        ctx = AgentContext(
            user_id=update.effective_user.id if update.effective_user else 0,
            user_message=text,
            command="ask",
            args={"text": text},
        )
        # Decide the right progress based on intent
        lower = text.lower().strip()
        if any(w in lower for w in ["buy", "sell", "long", "short", "ape", "dump", "load", "fade"]):
            await edit_status(status_msg, "🔍 Detecting trade intent…")
            await asyncio.sleep(0.3)
            await edit_status(status_msg, "📊 Fetching live market data…")
            await asyncio.sleep(0.3)
            await edit_status(status_msg, "🛡️ Validating risk…")
        elif any(w in lower for w in ["scan", "find", "autotrade", "best"]):
            await edit_status(status_msg, "🔍 Scanning top 50 USDT pairs…")
            await asyncio.sleep(0.3)
            await edit_status(status_msg, "🧮 Scoring candidates…")
            await asyncio.sleep(0.3)
            await edit_status(status_msg, "🤖 Asking Qwen for the final pick…")
        else:
            await edit_status(status_msg, "🧠 Asking Qwen…")
        try:
            response = await asyncio.to_thread(agent.handle, ctx)
        except Exception as e:
            logger.exception(f"Agent error: {e}")
            response = f"❌ Error: {e}"

        for chunk in _split_message(response):
            await update.message.reply_text(chunk, parse_mode="Markdown", disable_web_page_preview=True)

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Telegram bot error: {context.error}")

    async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Voice message handler: download .ogg, transcribe via Qwen ASR (or Whisper),
        then route the transcription through the same prompt flow as text."""
        if not update.message or not update.message.voice:
            return
        voice = update.message.voice
        await send_typing(update)
        status_msg = await send_status(update, "🎙️ Listening to your voice…")
        try:
            # Download the .ogg file from Telegram
            tg_file = await voice.get_file()
            ogg_bytes = await tg_file.download_as_bytearray()
            await edit_status(status_msg, "🧠 Transcribing audio (Qwen ASR)…")

            # Try the Qwen OpenAI-compatible /audio/transcriptions endpoint.
            # If unavailable, fall back to a friendly message.
            import os as _os
            import io as _io
            import base64 as _b64
            from urllib import request as _urlreq
            api_key = _os.environ.get("BITGET_QWEN_API_KEY", "")
            base_url = _os.environ.get("QWEN_BASE_URL", "https://hackathon.bitgetops.com/v1")
            transcript = None
            if api_key:
                try:
                    # multipart/form-data upload
                    boundary = "----OnisowoBoundary"
                    body = (
                        f"--{boundary}\r\n"
                        f"Content-Disposition: form-data; name=\"file\"; filename=\"voice.ogg\"\r\n"
                        f"Content-Type: audio/ogg\r\n\r\n"
                    ).encode() + bytes(ogg_bytes) + (
                        f"\r\n--{boundary}\r\n"
                        f"Content-Disposition: form-data; name=\"model\"\r\n\r\nqwen-audio-asr"
                        f"\r\n--{boundary}--\r\n"
                    ).encode()
                    req = _urlreq.Request(
                        f"{base_url}/audio/transcriptions",
                        data=body,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                        },
                        method="POST",
                    )
                    with _urlreq.urlopen(req, timeout=30) as resp:
                        result = json.loads(resp.read().decode())
                        transcript = result.get("text") or result.get("transcript")
                except Exception as e:
                    logger.warning(f"Qwen ASR failed: {e}")

            if not transcript:
                # Friendly fallback
                await edit_status(status_msg, "⚠️ Voice transcription unavailable. Try `/llm` to verify LLM, or send the message as text.")
                return

            # Got the transcript — show it, then route through the prompt flow
            await edit_status(status_msg, f"🎙️ Heard: \"{transcript}\"\n\n🧠 Processing…")
            ctx = AgentContext(
                user_id=update.effective_user.id if update.effective_user else 0,
                user_message=transcript,
                command="ask",
                args={"text": transcript},
            )
            try:
                response = await asyncio.to_thread(agent.handle, ctx)
            except Exception as e:
                logger.exception(f"Agent error after voice: {e}")
                response = f"❌ Error: {e}"
            if response and len(response) <= 3800:
                await edit_status(status_msg, f"🎙️ Heard: \"{transcript}\"\n\n{response}")
            else:
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                await update.message.reply_text(f"🎙️ Heard: \"{transcript}\"\n\n{response}", parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            logger.exception(f"voice_handler failed: {e}")
            await edit_status(status_msg, f"❌ Voice error: {e}")

    # Register handlers

    app.add_handler(MessageHandler(filters.COMMAND, cmd_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, freeform_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, voice_handler))
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
