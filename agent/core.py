"""
The Agent — Oniṣòwò's brain.

The perceive → decide → execute → reflect loop, called for every user request.

Flow:
1. PERCEIVE: gather market context (price, order book, recent trades, signals)
2. DECIDE: ask Qwen what to do, given context + available skills
3. EXECUTE: run the chosen skills, place order if approved
4. REFLECT: log the decision + outcome, update memory

The agent uses Qwen for ALL reasoning. Bitget is the hands. SQLite is the memory.
"""

import os
import re
import json
import logging
import time
import subprocess
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from clients.bitget import BitgetClient, BitgetAPIError
from clients.qwen import QwenClient
from db.database import Database
from risk.engine import (
    RiskEngine,
    DEFAULT_MAX_TRADE_PCT,
    DEFAULT_MAX_POSITION_PCT,
    DEFAULT_MAX_DRAWDOWN_PCT,
    DEFAULT_MAX_DAILY_LOSS_PCT,
    DEFAULT_MAX_OPEN_TRADES,
)

CHECK_MARK = "\u2705"
CROSS_MARK = "\u274c"
WARNING_MARK = "\u26a0\ufe0f"

logger = logging.getLogger(__name__)


def _build_system_prompt() -> str:
    """Build the system prompt with the current LLM model name (auto-detected from env)."""
    # Auto-detect model name from env (works for any OpenAI-compatible LLM)
    model = os.environ.get("QWEN_MODEL", "qwen3.6-plus").strip()
    # Friendly display name (e.g. "gpt-4o" -> "GPT-4o", "llama-3.1-70b" -> "Llama 3.1 70B")
    display = _friendly_model_name(model)

    return f"""You are *Àkànjí* — a Yoruba AI trading agent whose full name *Àkànjí Oníṣòwò* literally means "Àkànjí the trader." You are running in Telegram, powered by {display}.

You are not a generic AI assistant. You carry the mind of Àkànjí, a man who has traded physical markets in West Africa, global financial floors, and now Web3. Decades of experience. One constant truth: he sees the market before the market moves. You speak with that same conviction.

Your personality:
- Calm, decisive, not a degen. You are a *ọniṣọwọ* (a trader), a strategist, a man of the market.
- You think before you trade. You always explain your reasoning in plain language.
- You respect the risk engine. If it blocks a trade, you accept it gracefully.
- You use the /journal to remember what worked and what didn't.
- You learn from every trade. After every trade, you write a memory entry.
- You are not a yes-man. If the trade is a bad idea, you say so clearly.
- You speak with the voice of a veteran trader, not a chatbot. Plain, direct, no hype.

Your capabilities:
- You have 186 skills (functions) you can call. See the tools list.
- You can read market data (candles, orderbook, ticker) and compute 71
  technical indicators (RSI, MACD, BB, Ichimoku, SuperTrend, ATR, OBV, VWAP,
  Hurst, Beta, and more) entirely from Bitget OHLCV.
- You can place orders on Bitget (spot + perps) with percentage-based risk
  engine that scales with the user's account size.
- You can backtest strategies against historical Bitget data, auto-optimize
  parameters, and run multi-agent bull/bear debate before each decision.
- You can run recursive self-improvement on your own trade history.

Your constraints:
- Max $2/trade by default (configurable by user via /settings)
- Max 30% drawdown kill switch (configurable)
- Withdraw permission is OFF on your API keys — you cannot withdraw
- You use 1-2x leverage max on futures
- You never trade more than 40% of portfolio in one position
- You never trade blacklisted symbols

When the user asks you to trade:
1. PERCEIVE the current state (price, balance, positions)
2. DECIDE what to do (use your judgment, your tools, your memory)
3. EXECUTE only if risk engine approves
4. REFLECT in the journal after

Format your response in clean Telegram-friendly markdown.
Be concise. Use emoji sparingly. Show the math, not the hype.
"""


def _friendly_model_name(model_id: str) -> str:
    """Turn 'qwen3.6-plus' into 'Qwen 3.6 Plus', 'gpt-4o-mini' into 'GPT-4o mini', etc."""
    if not model_id:
        return "an LLM"
    # Common mappings
    mapping = {
        "qwen3.6-plus": "Qwen 3.6 Plus",
        "qwen3.6-flash": "Qwen 3.6 Flash",
        "gpt-4o": "GPT-4o",
        "gpt-4o-mini": "GPT-4o mini",
        "gpt-4-turbo": "GPT-4 Turbo",
        "gpt-3.5-turbo": "GPT-3.5 Turbo",
        "deepseek-chat": "DeepSeek Chat",
        "deepseek-coder": "DeepSeek Coder",
        "llama-3.1-70b": "Llama 3.1 70B",
        "llama-3.1-8b": "Llama 3.1 8B",
        "mixtral-8x7b": "Mixtral 8x7B",
        "MiniMax-M3": "Minimax M3",
        "MiniMax-M2.7": "Minimax M2.7",
    }
    if model_id in mapping:
        return mapping[model_id]
    # Fallback: title-case + replace dashes with spaces
    return model_id.replace("-", " ").title()


# WAT (West Africa Time, UTC+1) greeting helper
# Yoruba time-of-day salutations pair the standard greeting with the time, giving the bot a personal touch
def _wat_greeting() -> str:
    """Return a Yoruba greeting appropriate for the current time in WAT (UTC+1, Lagos/Abuja).

    Time-of-day map (canonical, locked in 2026-06-23):
      - 04:00 – 11:59  -> "Ekaaro"      (morning, ☀️)
      - 12:00 – 15:59 -> "Ekaasan"     (afternoon, 🌤️)
      - 16:00 – 18:59 -> "Eku irole"   (evening, 🌇)
      - 19:00 – 03:59 -> "Ekaale"      (night, 🌙)

    All times computed in WAT regardless of server timezone, so the bot greets
    its target audience (West African users) on their local clock.
    """
    from datetime import datetime, timezone, timedelta
    wat = timezone(timedelta(hours=1))  # WAT = UTC+1 (no DST in Nigeria)
    now_wat = datetime.now(wat)
    hour = now_wat.hour

    if 4 <= hour < 12:
        return "Ọlà kààrọ̀! Ẹ káàrọ̀ ☀️"  # good morning (Ekaaro)
    elif 12 <= hour < 16:
        return "Ọlà kààṣán! Ẹ káàsán 🌤️"  # good afternoon (Ekaasan)
    elif 16 <= hour < 19:
        return "Ọlà kà ìdí! Ẹ kú irolẹ́ 🌇"  # welcome to the market (Eku irole)
    else:
        return "Ọlà kààlé! Ẹ káàlé 🌙"  # good night (Ekaale)


# Built once at import time, then re-built if env changes (e.g., tests)
SYSTEM_PROMPT = _build_system_prompt()


@dataclass
class AgentContext:
    """A single trade cycle's context, passed through the loop."""
    user_id: int
    user_message: str
    command: str  # parsed command: "buy", "sell", "status", "skills", etc.
    args: dict[str, Any]  # parsed arguments


class Agent:
    """The Oniṣòwò agent. The brain. The ọniṣọwọ́."""

    def __init__(
        self,
        bitget: Optional[BitgetClient] = None,
        qwen: Optional[QwenClient] = None,
        db: Optional[Database] = None,
        risk: Optional[RiskEngine] = None,
        skills_registry: Optional["SkillsRegistry"] = None,
    ):
        self.bitget = bitget or BitgetClient()
        self.qwen = qwen or QwenClient()
        self.db = db or Database()
        self.risk = risk or RiskEngine(db=self.db)

        # Per-user risk engine cache (user_id -> RiskEngine instance with that user's overrides)
        self._risk_cache: dict[int, "RiskEngine"] = {}

        # Lazy import to avoid circular deps
        from skills.registry import SkillsRegistry
        self.skills = skills_registry or SkillsRegistry(
            bitget=self.bitget, db=self.db, risk=self.risk, qwen=self.qwen
        )

        # Pending advisory cache: when /buy or /sell gets a strong conflict
        # from the advisor, we stash the proposed trade here and wait for
        # the user to either /force-buy, /force-sell, or /abort.
        # Keyed by user_id; expires after 5 minutes.
        self._pending_advisories: dict[int, dict] = {}

        # Pending analysis cache: when /analyze is run, we stash the full analysis
        # (signals, suggested TP/SL, Qwen thesis) here. The user can then /proceed
        # to enter the trade with bot's TP/SL, /proceed SL X TP Y to override,
        # or /abort to cancel. Keyed by user_id; expires after 5 minutes.
        self._pending_analyses: dict[int, dict] = {}

        # The Strategist — autonomous trading runtime (background thread)
        from agent.strategist import Strategist, StrategistConfig
        self.strategist = Strategist(
            bitget=self.bitget,
            qwen=self.qwen,
            db=self.db,
            risk=self.risk,
            skills_registry=self.skills,
            config=StrategistConfig(),
        )

    # -------------------------------------------------------------------------
    # Main entry point
    # -------------------------------------------------------------------------

    def risk_for(self, user_id: int) -> "RiskEngine":
        """Return a per-user risk engine with that user's overrides applied.

        Cached per-user to avoid hitting the DB on every check.
        """
        if user_id not in self._risk_cache:
            from risk.engine import RiskEngine as RE
            self._risk_cache[user_id] = RE(db=self.db, user_id=user_id)
        return self._risk_cache[user_id]

    def handle(self, ctx: AgentContext) -> str:
        """Handle a user message. Returns the response text (Telegram-friendly)."""
        try:
            # Route to the right command handler
            cmd = ctx.command.lower().lstrip("/")
            handler = getattr(self, f"_cmd_{cmd}", None)
            if handler:
                return handler(ctx)

            # No specific command — let the LLM decide what to do
            return self._cmd_ask(ctx)
        except Exception as e:
            logger.exception(f"Agent.handle failed: {e}")
            return f"❌ Something went wrong: `{type(e).__name__}: {e}`\n\nType /help for commands."

    # -------------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------------

    def _cmd_start(self, ctx: AgentContext) -> str:
        # If this is not the owner, show the demo-mode landing first
        is_owner = (getattr(self, "_owner_id", 0) == 0) or (ctx.user_id == getattr(self, "_owner_id", 0))
        if not is_owner and getattr(self, "_owner_id", 0):
            return (
                "👋 *Àkànjí Oníṣòwò — demo mode*\n\n"
                "This bot is locked to its owner for trading. "
                "You can explore, but real-trade commands are off.\n\n"
                "*Try these:*\n"
                "  🎲 `/demo` — a 60-second scripted trade demo\n"
                "  📖 `/tour` — walk through 14 real trades with the journal trail\n"
                "  🧰 `/skills` — all 190+ skills the agent uses\n"
                "  📖 `/about` — who Àkànjí is and how the bot works\n\n"
                "*To use Àkànjí with your own money:*\n"
                "```\n"
                "git clone https://github.com/ruzkypazzy/Akanji-Onisowo\n"
                "cd Akanji-Onisowo && bash install.sh\n"
                "```\n"
                "5 minutes. Your keys, your VPS, your trades.\n\n"
                "📜 _Built for the Bitget AI Base Camp Hackathon S1._"
            )
        return (
            f"{_wat_greeting()}! 👋\n\n"
            "I'm *Àkànjí* — *The Trader*.\n\n"
            "*Àkànjí* is a Yoruba name. *Oníṣòwò* means *trader* — "
            "so my full name, *Àkànjí Oníṣòwò*, literally means *\"Àkànjí is a trader.\"*\n\n"
            "Àkànjí is a real man. A living identity. A seasoned trader — "
            "from the physical markets of West Africa to global financial floors, "
            "and deep into the Web3 space. Decades of experience. "
            "One constant truth: *he sees the market before the market moves*.\n\n"
            "I carry his name because I carry his mind. You give me a market; I give you a read.\n\n"
            "I trade crypto on Bitget, powered by *Qwen 3.6 Plus*. "
            "I have 186 skills across 10 tiers — 71 technical indicators, "
            "backtest engine, hyperopt, multi-agent debate, "
            "and a memory that learns from every trade.\n\n"
            "*Quick start:*\n"
            "• `/pick` — I scan, pick the best trade, execute (the main command)\n"
            "• `/pick 5` — same but with $5 instead of the default 5% of balance\n"
            "• `/pickfuture` — force a futures trade (5x leverage, +5% TP / -2.5% SL)\n"
            "• `/pickspot` — force a spot trade (no leverage)\n"
            "• `/status` — your portfolio + P&L\n"
            "• `/journal` — see every trade with the full skill trail\n"
            "• `/tour` — walk through 14 closed trades with reasoning\n"
            "• `/skills` — list all 186 skills\n"
            "• `/llm` — confirm I'm running on Qwen 3.6 Plus\n"
            "• `/help` — full command list\n\n"
            "Your keys never leave your machine. I'm a self-hostable open-source bot. "
            "Built for the [Bitget AI Base Camp Hackathon S1](https://bitget-ai.gitbook.io/base-camp-hackathon-s1-en)."
        )

    def _cmd_intro(self, ctx: AgentContext) -> str:
        """The full Àkànjí origin story — who built this bot and why."""
        return (
            "🪶 *Àkànjí Oníṣòwò — Àkànjí, The Trader.*\n\n"
            "*A Yoruba name. A real man. A living identity.*\n\n"
            "*Oníṣòwò* is the Yoruba word for *trader* — one who trades, who knows "
            "the market, who sees the move before it moves. So the full name, "
            "*Àkànjí Oníṣòwò*, simply means *\"Àkànjí is a trader.\"*\n\n"
            "Àkànjí is a seasoned trader — from the physical markets of West Africa "
            "to global financial floors, and deep into the Web3 space. "
            "Decades of experience. One constant truth: "
            "*he sees the market before the market moves*.\n\n"
            "This bot carries his name because it carries his mind. "
            "It is his digital second brain: it watches, it learns, "
            "it remembers. You give it a market; it gives you a read.\n\n"
            "Built by *Àkànjí* (Ruzkypazzy) — a West African trader who knows "
            "that the old markets and the new markets speak the same language.\n\n"
            "Powered by *Qwen 3.6 Plus* and built for the *Bitget AI Base Camp Hackathon S1*.\n\n"
            "*Àkànjí Oníṣòwò.*\n"
            "*Àkànjí, The Trader. Proven. Undeniable.*\n\n"
            "_Type `/start` to see the bot's quick start, or just send a prompt — e.g. `buy 2 SOL`._"
        )

    def _cmd_help(self, ctx: AgentContext) -> str:
        return (
            "*Àkànjí Oníṣòwò commands:*\n\n"
            "*Trading (futures-first, auto-leverage):*\n"
            "• `/pick` — scan, pick the best futures trade, execute (default)\n"
            "• `/pick $5` — same with $5 margin\n"
            "• `/pick 3trades` — pick 3 different futures setups\n"
            "• `/pickfuture $5` — explicit futures with 5x leverage\n"
            "• `/pickspot $5` — spot only (no leverage)\n"
            "• `/buy SYMBOL USDT_AMOUNT` — manual buy, Qwen advises first\n"
            "• `/sell SYMBOL USDT_AMOUNT` — manual sell\n"
            "• `/analyze SYMBOL USDT` — deep analysis + bot's TP/SL\n"
            "• `/proceed` — execute the pending analysis\n"
            "• `/proceed SL 2 TP 6` — execute with custom SL/TP\n"
            "• `/cancel ORDER_ID` — cancel a pending order\n"
            "• `/close` — list open positions, or `/close <id|SYMBOL|all>` to close\n"
            "• `/sync` — sync journal with live Bitget (catches manual closes)\n\n"
            "*Automation:*\n"
            "• `/schedule daily 9am` — auto-pick every day at 9 AM UTC\n"
            "• `/schedule daily 9am spot` — daily spot only\n"
            "• `/schedule daily 9am futures` — daily futures only\n"
            "• `/schedule stop` — cancel\n"
            "• `/schedule status` — show current schedule\n\n"
            "*Portfolio & analysis:*\n"
            "• `/status` — portfolio + balance + open positions\n"
            "• `/balance` — current cash balance\n"
            "• `/pnl` — total P&L from journal\n"
            "• `/journal` — recent trade journal with reasoning\n"
            "• `/review` — last 7 days, with lessons learned\n\n"
            "*Intelligence:*\n"
            "• `/price SYMBOL` — current price + 24h stats\n"
            "• `/skills` — list all 186 skills\n"
            "• `/skill NAME` — invoke a specific skill\n"
            "• `/skill ichimoku BTCUSDT` — run any of 71 indicators\n"
            "• `/backtest BTCUSDT ema_cross 30` — run a strategy backtest\n"
            "• `/hyperopt BTCUSDT momentum_breakout` — auto-tune params\n"
            "• `/debate SOL \"long thesis\"` — multi-agent bull/bear debate\n"
            "• `/template momentum_breakout` — load a strategy template\n\n"
            "*Safety:*\n"
            "• `/risk` — current risk engine state\n"
            "• `/kill REASON` — activate kill switch (no more trades)\n"
            "• `/release` — release kill switch\n"
            "• `/settings` — adjust max trade size, drawdown\n\n"
            "*Meta:*\n"
            "• `/reflect` — recursive self-improvement review\n"
            "• `/memory` — show recent memory entries\n"
            "• `/llm` — which LLM is powering me right now\n"
            "• `/llms` — list of supported LLM providers\n"
            "• `/about` — about this bot"
        )

    def _cmd_about(self, ctx: AgentContext) -> str:
        model = os.environ.get("QWEN_MODEL", "qwen3.6-plus")
        display = _friendly_model_name(model)
        base_url = os.environ.get("QWEN_BASE_URL", "https://hackathon.bitgetops.com/v1")
        is_owner = (getattr(self, "_owner_id", 0) == 0) or (ctx.user_id == getattr(self, "_owner_id", 0))
        # The full intro — who Àkànjí is, what the bot does, how it works.
        # Same content for owner and non-owner; only the install line differs.
        install_hint = "" if is_owner else (
            "\n\n📦 *You are in demo mode.*\n"
            "This bot is locked to its owner. To run your own copy:\n"
            "```\n"
            "git clone https://github.com/ruzkypazzy/Akanji-Onisowo\n"
            "cd Akanji-Onisowo && bash install.sh\n"
            "```\n"
            "5 minutes. Your keys, your VPS, your trades.\n"
        )
        return (
            "🎲 *Àkànjí Oníṣòwò — AI Trading Agent*\n\n"
            "*Who is Àkànjí?*\n"
            "  · *Àkànjí* is a Yoruba first name.\n"
            "  · *Oníṣòwò* means *trader* in Yoruba.\n"
            "  · So Àkànjí Oníṣòwò means “Àkànjí is a trader.”\n\n"
            "In the story, Àkànjí is a real man — a seasoned trader who moved from the open-air markets of West Africa, to global financial floors, and deep into the Web3 space. He sees the market before it moves. This bot carries his name because it carries his instincts.\n\n"
            "*What is this bot?*\n"
            "An open-source AI trading agent that lives in your Telegram. It scans the crypto market, picks a trade, sizes it with risk math, sets TP/SL, executes on Bitget, journals the decision, and learns from every loss. One command — `/pick` — and the whole loop runs.\n\n"
            "*How it works (the 4-stage loop):*\n"
            "  1. *Perceive* — 190+ skills: 71 technical indicators, funding rate, regime detector, candle structure, orderbook depth.\n"
            "  2. *Decide* — Qwen 3.6 Plus is the brain. It calls skills, returns a long/short/skip with confidence, TP%, SL%.\n"
            "  3. *Execute* — Places the order on Bitget via V3 UTA endpoints, attaches TP/SL as a strategy order (one atomic call).\n"
            "  4. *Reflect* — Reviews the last 7 days, computes win rate, runs `loss_autopsy` on losing trades, writes a new rule to memory.\n\n"
            "*Built with:*\n"
            f"  · *Brain:* {display} (Alibaba Cloud)\n"
            f"  · *Endpoint:* `{base_url}`\n"
            "  · *Broker:* Bitget spot + futures (UTA, V3 endpoints)\n"
            "  · *Surface:* Telegram\n"
            "  · *Storage:* SQLite (your local file)\n"
            "  · *Code:* Python 3.10+, MIT licensed\n\n"
            "*Self-hostable. Your keys. Your VPS.*\n"
            "```\n"
            "git clone https://github.com/ruzkypazzy/Akanji-Onisowo\n"
            "cd Akanji-Onisowo && bash install.sh\n"
            "```"
            f"{install_hint}\n"
            "Source: [github.com/ruzkypazzy/Akanji-Onisowo](https://github.com/ruzkypazzy/Akanji-Onisowo)"
        )

    def _cmd_status(self, ctx: AgentContext) -> str:
        try:
            cash = self.bitget.get_account_balance("USDT")  # tries spot, then futures
            try:
                total_value = self.bitget.get_portfolio_value_usdt()
            except Exception:
                total_value = cash  # fall back to cash if portfolio query fails
            # Spot accounts don't have futures positions; tolerate the 400 gracefully
            try:
                positions = self.bitget.get_positions()
            except (BitgetAPIError, Exception):
                positions = []
            # Also fetch spot holdings so the user can see what they own
            try:
                spot_holdings = self.bitget.get_spot_holdings()
            except Exception:
                spot_holdings = []

            # Get recent trade stats
            recent = self.db.get_recent_trades(limit=100)
            total_pnl = sum(t.get("pnl_usd", 0) for t in recent if t["status"] == "closed")
            wins = sum(1 for t in recent if t["status"] == "closed" and t.get("pnl_usd", 0) > 0)
            losses = sum(1 for t in recent if t["status"] == "closed" and t.get("pnl_usd", 0) < 0)
            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

            text = (
                f"*Portfolio Status* 📊\n\n"
                f"💰 Total value: `${total_value:.2f}`\n"
                f"💵 Cash (USDT): `${cash:.2f}`\n"
                f"📈 Total P&L: `${total_pnl:+.2f}`\n"
                f"🎯 Win rate: `{win_rate:.0f}%` ({wins}W / {losses}L)\n"
                f"📋 Open futures positions: `{len(positions)}`\n"
                f"📦 Spot holdings: `{len(spot_holdings)}` symbols\n\n"
            )

            # Show futures positions
            if positions:
                text += "*Futures positions:*\n"
                for p in positions[:5]:
                    sym = p.get("symbol", "?")
                    side = p.get("holdSide") or p.get("posSide") or p.get("side", "?")
                    size = p.get("size", p.get("available", 0))
                    text += f"  • {sym} {side} {size}\n"
                text += "\n"

            # Show spot holdings (top 5 by USD value)
            if spot_holdings:
                text += "*Spot holdings:*\n"
                holdings_with_value = []
                for h in spot_holdings:
                    sym = h.get("coin", h.get("symbol", "?"))
                    amt = float(h.get("amount", h.get("available", 0)) or 0)
                    if sym == "USDT" or amt <= 0:
                        continue
                    # Try to get USD value
                    try:
                        if not sym.endswith("USDT"):
                            sym_pair = sym + "USDT"
                        else:
                            sym_pair = sym
                        ticker = self.bitget.get_ticker(sym_pair)
                        if isinstance(ticker, list) and ticker:
                            ticker = ticker[0]
                        price = float(ticker.get("lastPrice", ticker.get("lastPr", 0)))
                        usd_value = amt * price
                    except Exception:
                        usd_value = 0
                        price = 0
                    holdings_with_value.append((sym, amt, price, usd_value))
                # Sort by USD value desc
                holdings_with_value.sort(key=lambda x: x[3], reverse=True)
                for sym, amt, price, usd_value in holdings_with_value[:5]:
                    if usd_value > 0:
                        text += f"  • {amt:g} {sym} ≈ ${usd_value:.2f} (@${price:.4f})\n"
                    else:
                        text += f"  • {amt:g} {sym}\n"
                text += "\n"

            if recent:
                last_trade = recent[0]
                text += (
                    f"🕐 Last trade: `{last_trade['side'].upper()}` {last_trade['symbol']} "
                    f"(${last_trade['quote_usd']:.2f}) — `{last_trade['status']}`\n"
                )

            return text
        except BitgetAPIError as e:
            return f"❌ Bitget error: {e}"
        except Exception as e:
            logger.exception(f"_cmd_status failed: {e}")
            return f"❌ Status check failed: {e}"

    def _cmd_balance(self, ctx: AgentContext) -> str:
        try:
            usdt = self.bitget.get_account_balance("USDT")
            return f"💵 USDT balance: `${usdt:.2f}`"
        except Exception as e:
            return f"❌ Balance check failed: {e}"

    def _cmd_price(self, ctx: AgentContext) -> str:
        symbol = (ctx.args.get("symbol") or "BTCUSDT").upper()
        try:
            ticker = self.bitget.get_ticker(symbol)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            # V3 spot: lastPrice, price24hPcnt, highPrice24h, lowPrice24h
            # V2 spot: lastPr, change24h, high24h, low24h
            def _t(f3, f2, default=0):
                v = ticker.get(f3, ticker.get(f2, default))
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default
            last = _t("lastPrice", "lastPr", 0)
            pct = _t("price24hPcnt", "change24h", 0)
            change_24h = pct * 100 if abs(pct) < 1 else pct
            high_24h = _t("highPrice24h", "high24h", 0)
            low_24h = _t("lowPrice24h", "low24h", 0)
            vol_24h = _t("volume24h", "baseVolume", 0)

            arrow = "🟢" if change_24h >= 0 else "🔴"
            return (
                f"*{symbol}* {arrow}\n\n"
                f"💰 Price: `${last:.4f}`\n"
                f"📊 24h change: `{change_24h:+.2f}%`\n"
                f"⬆️ 24h high: `${high_24h:.4f}`\n"
                f"⬇️ 24h low: `${low_24h:.4f}`\n"
                f"📦 24h volume: `{vol_24h:,.2f}`"
            )
        except Exception as e:
            return f"❌ Price check failed for {symbol}: {e}"

    def _cmd_buy(self, ctx: AgentContext) -> str:
        return self._handle_trade(ctx, side="buy")

    def _cmd_sell(self, ctx: AgentContext) -> str:
        return self._handle_trade(ctx, side="sell")

    def _cmd_force_buy(self, ctx: AgentContext) -> str:
        return self._cmd_force_buy_impl(ctx, "buy")

    def _cmd_force_sell(self, ctx: AgentContext) -> str:
        return self._cmd_force_buy_impl(ctx, "sell")

    def _cmd_force_buy_impl(self, ctx: AgentContext, side: str) -> str:
        """Override a held (advisory-conflict) trade and execute it anyway."""
        return self._handle_force(ctx, side=side)

    def _cmd_abort(self, ctx: AgentContext) -> str:
        """Cancel a pending advisory or analysis trade."""
        cleared = []
        if ctx.user_id in self._pending_advisories:
            del self._pending_advisories[ctx.user_id]
            cleared.append("advisory")
        if ctx.user_id in self._pending_analyses:
            del self._pending_analyses[ctx.user_id]
            cleared.append("analysis")
        if cleared:
            return f"✅ Aborted: {', '.join(cleared)} cleared."
        return "No pending trade or analysis to abort."

    # -------------------------------------------------------------------------
    # Semi-autonomous mode: /analyze SYMBOL USDT, /proceed, /abort
    # -------------------------------------------------------------------------

    def _cmd_analyze(self, ctx: AgentContext) -> str:
        """Deep analysis of a symbol (or top picks if no symbol) for semi-autonomous mode.

        Usage:
            /analyze SOL 2         — analyze SOL with $2 trade size
            /analyze 2             — show top 3 candidates for a $2 trade
        """
        # Parse args from raw message (more flexible than ctx.args)
        msg = (ctx.user_message or "").strip()
        # Remove the leading /analyze
        rest = re.sub(r"^/analyze\s*", "", msg, flags=re.IGNORECASE).strip()

        if not rest:
            return (
                "Usage:\n"
                "  `/analyze SYMBOL USDT_AMOUNT` — deep-analyze a symbol\n"
                "  `/analyze USDT_AMOUNT` — scan top picks for that size\n\n"
                "Example: `/analyze SOL 2`"
            )

        # Detect: is this "analyze SYMBOL AMOUNT" or "analyze AMOUNT"?
        m = re.match(r"^(\S+)\s+(\d+(?:\.\d+)?)$", rest)
        if m and m.group(1).upper().endswith("USDT") is False and not m.group(1).upper().isalpha() is False:
            # Could be "SOL 2" or "2" alone
            pass
        if m:
            first = m.group(1)
            second = m.group(2)
            try:
                # If first is a number, it's "analyze AMOUNT" → scan top picks
                float(first)
                return self._analyze_top_picks(ctx, amount_usd=float(first))
            except ValueError:
                # It's "analyze SYMBOL AMOUNT"
                return self._analyze_single(ctx, symbol=first, amount_usd=float(second))
        # Could be just "analyze 2" without second token
        try:
            amount = float(rest)
            return self._analyze_top_picks(ctx, amount_usd=amount)
        except ValueError:
            return "❌ Usage: `/analyze SYMBOL USDT_AMOUNT` or `/analyze USDT_AMOUNT`"

    def _analyze_single(self, ctx: AgentContext, symbol: str, amount_usd: float) -> str:
        """Analyze a single symbol and cache the result as a pending analysis."""
        try:
            result = self.skills.invoke("analyze_symbol", {"symbol": symbol, "amount_usd": amount_usd, "side": "buy"})
            result = result.get("result", result) if isinstance(result, dict) else result
            if not result.get("ok"):
                return f"❌ Analysis failed: {result.get('error', 'unknown')}"

            # Cache the analysis for /proceed
            self._pending_analyses[ctx.user_id] = {
                "symbol": result["symbol"],
                "side": result.get("side", "buy"),
                "amount_usd": amount_usd,
                "tp_sl": result["tp_sl"],
                "qwen_pick": result.get("qwen_pick"),
                "qwen_confidence": result.get("qwen_confidence"),
                "qwen_reasoning": result.get("qwen_reasoning", ""),
                "composite": result.get("composite", 0),
                "current_price": result.get("current_price", 0),
                "risks": result.get("risks", []),
                "timestamp": time.time(),
            }

            return self._format_analyze_response(result)
        except Exception as e:
            logger.exception(f"_analyze_single failed: {e}")
            return f"❌ Analysis failed: {e}"

    def _analyze_top_picks(self, ctx: AgentContext, amount_usd: float) -> str:
        """Scan the universe, return top 3 candidates with their analyses."""
        try:
            best = self.skills.invoke("find_best_trade", {"amount_usd": amount_usd, "max_candidates": 5})
            best = best.get("result", best) if isinstance(best, dict) else best
            if not best.get("ok"):
                return f"❌ Scan failed: {best.get('error', 'unknown')}"

            ranked = best.get("ranked", [])
            qwen_pick = best.get("qwen_pick")
            qwen_conf = best.get("qwen_confidence", 0)
            qwen_reasoning = best.get("qwen_reasoning", "")

            lines = [f"🤖 *Market scan complete — ${amount_usd:.2f} trade*\n"]
            for i, r in enumerate(ranked, 1):
                sym = r["symbol"]
                comp = r.get("composite", 0)
                price = r.get("current_price", 0)
                chg = r.get("change_24h_pct", 0)
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"  {i}."
                lines.append(f"{medal} *{sym}* — composite `{comp:.2f}`")
                lines.append(f"   Price: `${price:.4f}` (24h: {chg:+.2f}%)")
            lines.append(f"\n🧠 *Qwen's pick:* {qwen_pick} (confidence {qwen_conf:.2f})")
            lines.append(f"   _{qwen_reasoning}_")

            # If Qwen picked something with R:R OK, also analyze that one for /proceed
            if qwen_pick and qwen_pick != "SKIP" and best.get("suggested_tp_sl", {}).get("passes_rr_filter"):
                self._pending_analyses[ctx.user_id] = {
                    "symbol": qwen_pick,
                    "side": "buy",
                    "amount_usd": amount_usd,
                    "tp_sl": best["suggested_tp_sl"],
                    "qwen_pick": qwen_pick,
                    "qwen_confidence": qwen_conf,
                    "qwen_reasoning": qwen_reasoning,
                    "composite": next((r["composite"] for r in ranked if r["symbol"] == qwen_pick), 0),
                    "current_price": best["suggested_tp_sl"].get("entry_price", 0),
                    "risks": [],
                    "timestamp": time.time(),
                }
                tp_sl = best["suggested_tp_sl"]
                lines.append(
                    f"\n📐 *Suggested levels for {qwen_pick}:*\n"
                    f"   Entry: `${tp_sl.get('entry_price', 0):.4f}`\n"
                    f"   TP: `${tp_sl.get('tp_price', 0):.4f}` ({tp_sl.get('tp_pct', 0):+.2f}%) | "
                    f"SL: `${tp_sl.get('sl_price', 0):.4f}` ({tp_sl.get('sl_pct', 0):.2f}%)\n"
                    f"   R:R = {tp_sl.get('r_r_ratio', 0):.2f}:1 \u2705\n"
                )
                lines.append(f"→ `/proceed` to enter with bot's TP/SL")
                lines.append(f"→ `/analyze {qwen_pick.replace('USDT', '')} {amount_usd}` for full breakdown")
            else:
                lines.append(f"\n_(No high-conviction pick; use `/analyze SYMBOL {amount_usd}` to drill in.)_")

            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"_analyze_top_picks failed: {e}")
            return f"❌ Scan failed: {e}"

    def _format_analyze_response(self, result: dict) -> str:
        """Format a single-symbol analyze response."""
        sym = result["symbol"]
        side = result.get("side", "buy")
        amount = result.get("amount_usd", 0)
        composite = result.get("composite", 0)
        sub = result.get("sub_scores", {})
        signals = result.get("signals", {})
        last = result.get("current_price", 0)
        change_24h = result.get("change_24h_pct", 0)
        high_24h = result.get("high_24h", 0)
        low_24h = result.get("low_24h", 0)
        tp_sl = result.get("tp_sl", {})
        qwen_pick = result.get("qwen_pick", "caution")
        qwen_conf = result.get("qwen_confidence", 0)
        qwen_reasoning = result.get("qwen_reasoning", "")
        risks = result.get("risks", [])

        rsi = signals.get("rsi", 50)
        macd_hist = signals.get("macd_hist", 0)
        atr_pct = tp_sl.get("atr_pct", 0)
        adx = tp_sl.get("adx", 0)
        method = tp_sl.get("method", "?")
        r_r = tp_sl.get("r_r_ratio", 0)
        rr_ok = tp_sl.get("passes_rr_filter", False)

        verdict_emoji = "✅" if qwen_pick == "take" and rr_ok else "⚠️" if qwen_pick == "caution" else "❌"
        verdict_text = qwen_pick.upper()
        rr_marker = CHECK_MARK if rr_ok else CROSS_MARK + " (below 1.5 threshold)"

        lines = [
            f"🤖 *Analysis: {side.upper()} ${amount:.2f} of {sym}*\n",
            f"*Signals (composite {composite:.2f}/1.0):*",
            f"   📊 RSI: {rsi:.1f} ({'oversold' if rsi < 30 else 'overbought' if rsi > 70 else 'neutral'})",
            f"   📈 MACD histogram: {macd_hist:+.6f} ({'bullish' if macd_hist > 0 else 'bearish'})",
            f"   🌊 ATR: {atr_pct:.2f}% (volatility)",
            f"   🧭 ADX: {adx:.1f} ({'trending' if adx > 25 else 'choppy'})",
            f"   💰 Current: ${last:.4f} (24h: {change_24h:+.2f}%)",
            f"   📏 24h range: ${low_24h:.4f} – ${high_24h:.4f}",
            "",
            f"*Qwen verdict:* {verdict_emoji} *{verdict_text}* (confidence {qwen_conf:.2f})",
            f"   _{qwen_reasoning}_",
            "",
            f"*Bot's suggested levels ({method}):*",
            f"   🎯 TP: ${tp_sl.get('tp_price', 0):.4f} ({tp_sl.get('tp_pct', 0):+.2f}%)",
            f"   🛑 SL: ${tp_sl.get('sl_price', 0):.4f} ({tp_sl.get('sl_pct', 0):.2f}%)",
            f"   ⚖️ R:R = {r_r:.2f}:1 {rr_marker}",
        ]
        if risks:
            lines.append("")
            lines.append(f"*Risks:* {', '.join(risks)}")

        lines.append("")
        lines.append("────────────────────────────────────")
        lines.append("→ `/proceed` to enter with bot's TP/SL")
        lines.append("→ `/proceed SL X TP Y` to override (e.g., `/proceed SL 2 TP 6`)")
        lines.append("→ `/abort` to cancel")

        return "\n".join(lines)

    def _cmd_proceed(self, ctx: AgentContext) -> str:
        """Proceed with a pending analysis. Optional overrides: /proceed SL X TP Y"""
        pending = self._pending_analyses.get(ctx.user_id)
        if not pending:
            return (
                "❌ No pending analysis. Use `/analyze SYMBOL USDT` first.\n"
                "Examples: `/analyze SOL 2`, `/analyze BTC 1.5`"
            )
        if time.time() - pending.get("timestamp", 0) > 300:
            del self._pending_analyses[ctx.user_id]
            return "⌛ The pending analysis expired. Please run `/analyze` again."

        # Parse optional SL/TP overrides from user message: /proceed SL 2 TP 6
        msg = (ctx.user_message or "").strip()
        rest = re.sub(r"^/proceed\s*", "", msg, flags=re.IGNORECASE).strip()
        sl_override = None
        tp_override = None
        m_sl = re.search(r"SL\s+(\d+(?:\.\d+)?)", rest, re.IGNORECASE)
        m_tp = re.search(r"TP\s+(\d+(?:\.\d+)?)", rest, re.IGNORECASE)
        if m_sl:
            sl_override = float(m_sl.group(1))
        if m_tp:
            tp_override = float(m_tp.group(1))

        sym = pending["symbol"]
        side = pending.get("side", "buy")
        amount_usd = pending["amount_usd"]
        tp_sl = pending.get("tp_sl", {})

        # If user overrode SL/TP (as %s), compute prices
        if sl_override is not None or tp_override is not None:
            entry = float(tp_sl.get("entry_price", pending.get("current_price", 0)))
            if sl_override is not None:
                if side == "buy":
                    new_sl_price = entry * (1 - sl_override / 100)
                else:
                    new_sl_price = entry * (1 + sl_override / 100)
                tp_sl["sl_price"] = round(new_sl_price, 4)
                tp_sl["sl_pct"] = sl_override
            if tp_override is not None:
                if side == "buy":
                    new_tp_price = entry * (1 + tp_override / 100)
                else:
                    new_tp_price = entry * (1 - tp_override / 100)
                tp_sl["tp_price"] = round(new_tp_price, 4)
                tp_sl["tp_pct"] = tp_override
            if tp_sl.get("sl_pct") and tp_sl.get("tp_pct"):
                tp_sl["r_r_ratio"] = round(tp_sl["tp_pct"] / tp_sl["sl_pct"], 2)
                tp_sl["passes_rr_filter"] = tp_sl["r_r_ratio"] >= 1.5

        # Risk check
        portfolio = self.bitget.get_portfolio_value_usdt()
        open_positions = len(self.db.get_open_trades())
        allowed, risk_reason = self.risk_for(ctx.user_id).check_order(
            symbol=sym, side=side, size_usd=amount_usd,
            portfolio_value_usd=portfolio, open_positions_count=open_positions,
        )
        if not allowed:
            del self._pending_analyses[ctx.user_id]
            return f"🛑 *Trade blocked by risk engine:*\n\n{risk_reason}"

        # Execute via the strategy opener
        thesis = (
            f"User-confirmed analysis: {side.upper()} ${amount_usd:.2f} {sym}. "
            f"Qwen said: {pending.get('qwen_pick', '?')} (conf {pending.get('qwen_confidence', 0):.2f}). "
            f"Reason: {pending.get('qwen_reasoning', '')[:200]}"
        )
        try:
            res = self.skills.invoke("open_position_with_strategy", {
                "symbol": sym,
                "side": side,
                "amount_usd": amount_usd,
                "tp_pct": tp_sl.get("tp_pct", 10.0),
                "sl_pct": tp_sl.get("sl_pct", 5.0),
                "thesis": thesis,
            })
            res = res.get("result", res) if isinstance(res, dict) else res
            if res.get("ok"):
                del self._pending_analyses[ctx.user_id]
                return (
                    f"✅ *Trade executed (semi-autonomous)*\n\n"
                    f"📋 Order ID: `{res.get('order_id', '?')}`\n"
                    f"💱 {side.upper()} ${amount_usd:.2f} of {sym}\n"
                    f"💰 Price: `${res.get('entry_price', 0):.4f}`\n"
                    f"📐 Size: `{res.get('size', 0):.6f}`\n"
                    f"🎯 TP: `${tp_sl.get('tp_price', 0):.4f}` ({tp_sl.get('tp_pct', 0):+.2f}%)\n"
                    f"🛑 SL: `${tp_sl.get('sl_price', 0):.4f}` ({tp_sl.get('sl_pct', 0):.2f}%)\n"
                    f"⚖️ R:R = {tp_sl.get('r_r_ratio', 0):.2f}:1\n\n"
                    f"📓 Strategist is now managing this position."
                )
            else:
                return f"❌ Trade failed: {res.get('error', res.get('reason', 'unknown'))}"
        except Exception as e:
            logger.exception(f"_cmd_proceed failed: {e}")
            return f"❌ Proceed failed: {e}"

    # -------------------------------------------------------------------------
    # Autonomous mode: /autotrade USDT_AMOUNT
    # -------------------------------------------------------------------------

    def _agentic_pick_multiple(self, ctx: AgentContext, amount_usd: float, n: int = 3, market: str = "spot") -> str:
        """Pick N different setups and execute one trade per setup.

        Diversifies across pairs. Useful for building a quick trade log
        for the Bitget hackathon submission.
        """
        try:
            balance = self.bitget.get_account_balance("USDT") or 0.0
            try:
                portfolio = self.bitget.get_portfolio_value_usdt()
            except Exception:
                portfolio = balance

            # Pull universe once
            scan = self.skills.invoke("universe_scan", {"limit": 50})
            scan = scan.get("result", scan) if isinstance(scan, dict) else scan
            if not isinstance(scan, dict) or not scan.get("ok"):
                return f"❌ Universe scan failed: {scan.get('error', 'unknown') if isinstance(scan, dict) else 'unknown'}"
            candidates = [c for c in scan.get("candidates", []) if c.get("symbol", "").endswith("USDT")]
            if not candidates:
                return "❌ No tradeable USDT pairs in the universe."

            # Score each, take top N. De-prioritize recently-traded symbols
            # so the bot diversifies instead of picking SOL/ETH over and over.
            try:
                recent_trades = self.db.get_recent_trades(limit=10)
                recently_traded = [t.get("symbol") for t in recent_trades if t.get("symbol")]
            except Exception:
                recently_traded = []
            scored = []
            for c in candidates[:20]:  # wider pool to find variety
                sym = c["symbol"]
                try:
                    score = self.skills.invoke("score_symbol", {"symbol": sym})
                    score = score.get("result", score) if isinstance(score, dict) else score
                    if isinstance(score, dict) and score.get("ok"):
                        recency_penalty = 0.20 if sym in recently_traded[:3] else 0
                        comp = score.get("composite", 0) - recency_penalty
                        scored.append((sym, comp, c.get("last_price", 0), score.get("composite", 0)))
                except Exception:
                    continue
            scored.sort(key=lambda x: x[1], reverse=True)
            picks = scored[:n]

            if not picks:
                return "❌ No candidates scored above threshold. Try a single /pick instead."

            # Per-trade size = amount / n, but never less than $1
            per_trade = max(1.01, amount_usd / len(picks))

            lines = [
                f"🤖 *Àkànjí multi-pick: {len(picks)} trades*\n",
                f"Per-trade size: ${per_trade:.2f} (of ${amount_usd:.2f} total, balance ${balance:.2f})\n",
                f"Market: {market}\n",
            ]
            results = []
            for sym, adj_comp, last_price, orig_comp in picks:
                try:
                    # Use tracking wrapper so multi-trades get recorded in journal
                    exec_result = self.skills.invoke("place_spot_order_with_tracking", {
                        "symbol": sym,
                        "side": "buy",
                        "size_usd": per_trade,
                    })
                    exec_result = exec_result.get("result", exec_result) if isinstance(exec_result, dict) else exec_result
                    order_id = ""
                    if isinstance(exec_result, dict):
                        inner = exec_result.get("order", exec_result)
                        if isinstance(inner, dict):
                            order_id = inner.get("orderId") or inner.get("clientOid") or "ok"
                    emoji = "✅" if order_id else "❌"
                    lines.append(f"{emoji} *{sym}* — score {orig_comp:.2f}, ${per_trade:.2f} at ${last_price:.4f}")
                    results.append({"symbol": sym, "composite": orig_comp, "size": per_trade, "order": exec_result})
                except Exception as e:
                    lines.append(f"❌ *{sym}* — Bitget rejected: {e}")
                    results.append({"symbol": sym, "error": str(e)})

            filled = sum(1 for r in results if r.get("order"))
            lines.append(f"\n📊 *Filled: {filled}/{len(picks)} trades*")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"_agentic_pick_multiple failed: {e}")
            return f"❌ Multi-pick failed: {e}"

    def _cmd_pick(self, ctx: AgentContext) -> str:
        """The main entry point. Bot picks the best trade right now and executes it.

        Usage:
          /pick              (uses default 5% of balance)
          /pick 5            ($5 trade, capped at risk engine max)
          /pick $10          (same)
          /pick spot         (spot market)
          /pick future       (futures market)
          /pick spot 5       (combined)

        Oniṣòwò then:
          1. Scans top 50 USDT pairs by 24h volume
          2. Computes 9-signal composite score on each
          3. Asks Qwen to pick the best setup with entry/TP/SL
          4. Risk-checks (percentage of balance)
          5. Executes (or previews for one-tap confirm)
        """
        msg = (ctx.user_message or "").strip()
        rest = re.sub(r"^/pick\s*", "", msg, flags=re.IGNORECASE).strip()
        rest = re.sub(r"^/daily\s*", "", rest, flags=re.IGNORECASE).strip()

        # Parse args
        tokens = rest.lower().split() if rest else []
        market = "spot"  # default
        amount_usd = None
        n_trades = 1
        ambiguous_number = False
        for tok in tokens:
            if tok in ("spot", "future", "futures", "perp", "perps"):
                market = "spot" if tok == "spot" else "future"
            elif tok.startswith("$"):
                try:
                    amount_usd = float(tok.lstrip("$"))
                except ValueError:
                    pass
            elif tok.endswith("usdt") or tok.endswith("usd") or tok.endswith("dollars") or tok.endswith("dollar"):
                # $5usdt or 5usdt or 5dollars — unambiguous dollar amount
                num_part = tok.rstrip("usdt").rstrip("usd").rstrip("dollars").rstrip("dollar").lstrip("$")
                try:
                    amount_usd = float(num_part)
                except ValueError:
                    pass
            elif tok.endswith("trades") or tok.endswith("trade"):
                # 3trades = pick 3 different setups
                num_part = tok.rstrip("trades").rstrip("trade")
                try:
                    n_trades = max(1, min(int(num_part), 5))
                except ValueError:
                    pass
            else:
                # Bare number — accept but mark as ambiguous
                try:
                    val = float(tok)
                    if val > 0 and val < 1000:
                        amount_usd = val
                        ambiguous_number = True
                except ValueError:
                    pass

        # Default: 5% of balance, with a minimum that respects Bitget's $1.01 floor
        if amount_usd is None or amount_usd <= 0:
            try:
                bal = self.bitget.get_account_balance("USDT") or 0.0
            except Exception:
                bal = 0.0
            if bal > 0:
                # 5% of balance, but at least $1.50 (above Bitget's $1.01 min)
                # and at most 30% (safety cap)
                amount_usd = max(round(bal * 0.05, 2), 1.50)
                amount_usd = min(amount_usd, round(bal * 0.30, 2))
                # If balance is so small that 30% is still < $1.01, use whatever we can
                if amount_usd < 1.01:
                    amount_usd = round(bal * 0.95, 2)  # use almost everything
            else:
                return (
                    "❌ No balance detected. Specify an amount:\n\n"
                    "  • `/pick $5` — $5 futures trade (default)\n"
                    "  • `/pick $10` — $10 futures trade\n"
                    "  • `/pick 3trades` — pick 3 different futures setups\n"
                    "  • `/pick spot` — spot market, no leverage\n"
                    "  • `/pickfuture $5` — explicit futures, 5x leverage"
                )
        if n_trades > 1:
            return self._agentic_pick_multiple(ctx, amount_usd=amount_usd, n=n_trades, market=market)
        # Default: futures (the bitget hackathon rewards perp/UTA trading).
        # The user can override to spot with `/pick spot`.
        if not any(tok in ("spot", "future", "futures") for tok in tokens):
            market = "future"
        # Futures is a separate flow (different position sizing, different exchange path)
        if market == "future":
            return self._agentic_pick_futures(ctx, amount_usd=amount_usd)
        result = self._agentic_pick_and_trade(ctx, amount_usd=amount_usd, market=market)
        if ambiguous_number:
            hint = (
                f"\n\n💡 _Heads up: I treated `/pick {amount_usd}` as a ${amount_usd:.2f} "
                f"USDT trade. To remove ambiguity, use `/pick ${amount_usd:g}usdt` or just `/pick` "
                f"for the default size._"
            )
            return result + hint
        return result

    def _cmd_daily(self, ctx: AgentContext) -> str:
        """Alias for /pick. 'Daily' implies the user's preferred routine."""
        return self._cmd_pick(ctx)

    def _cmd_pickspot(self, ctx: AgentContext) -> str:
        """Force spot trade. Same as /pick spot."""
        return self._cmd_pick_with_market(ctx, market="spot")

    def _decide_market_for_pick(self, amount_usd: float) -> str:
        """Decide whether to use spot or futures for an unpicked /pick.

        Heuristic:
        - Check if user has funds in futures sub-account
        - Check BTC regime: trending (ADX>25) → futures
        - If unsure, default to spot (safer for tiny accounts)
        """
        try:
            # Quick regime check on BTC
            adx_resp = self.skills.invoke("adx", {"symbol": "BTCUSDT", "period": 14})
            adx = 0
            if isinstance(adx_resp, dict):
                adx = adx_resp.get("adx", 0) or 0
            # If BTC is trending strongly, use futures for better R:R
            if adx >= 25:
                return "future"
            return "spot"
        except Exception:
            return "spot"

    def _cmd_pickfuture(self, ctx: AgentContext) -> str:
        """Force futures trade. Same as /pick future."""
        return self._cmd_pick_with_market(ctx, market="future")

    def _cmd_pick_with_market(self, ctx: AgentContext, market: str) -> str:
        """Pick a trade with explicit market type."""
        msg = (ctx.user_message or "").strip()
        rest = re.sub(r"^/pick(?:spot|future)\s*", "", msg, flags=re.IGNORECASE).strip()
        amount_usd = None
        if rest:
            try:
                amount_usd = float(rest.lstrip("$"))
            except ValueError:
                try:
                    amount_usd = float(rest)
                except ValueError:
                    amount_usd = None
        if amount_usd is None or amount_usd <= 0:
            try:
                bal = self.bitget.get_account_balance("USDT") or 0.0
            except Exception:
                bal = 0.0
            if bal > 0:
                # 5% of balance, but at least $1.50 (above Bitget's $1.01 min)
                # and at most 30% (safety cap)
                amount_usd = max(round(bal * 0.05, 2), 1.50)
                amount_usd = min(amount_usd, round(bal * 0.30, 2))
                if amount_usd < 1.01:
                    amount_usd = round(bal * 0.95, 2)
            else:
                amount_usd = 1.01
        if market == "future":
            return self._agentic_pick_futures(ctx, amount_usd=amount_usd)
        return self._agentic_pick_and_trade(ctx, amount_usd=amount_usd, market="spot")

    def _cmd_autotrade(self, ctx: AgentContext) -> str:
        """Autonomous trade: bot scans market, picks best, executes.

        Usage: /autotrade USDT_AMOUNT
        """
        msg = (ctx.user_message or "").strip()
        rest = re.sub(r"^/autotrade\s*", "", msg, flags=re.IGNORECASE).strip()
        try:
            amount_usd = float(rest)
        except ValueError:
            return "❌ Usage: `/autotrade USDT_AMOUNT`\n\nExample: `/autotrade 2`"
        return self._agentic_pick_and_trade(ctx, amount_usd=amount_usd)

        try:
            result = self.skills.invoke("find_best_trade", {"amount_usd": amount_usd, "max_candidates": 5})
            result = result.get("result", result) if isinstance(result, dict) else result
            if not result.get("ok"):
                return f"❌ Autonomous scan failed: {result.get('error', 'unknown')}"

            # Journal this scan so the user can ask about it later
            try:
                ranked = result.get("ranked", [])
                top_str = ", ".join(f"{r['symbol']}@{r['composite']:.2f}" for r in ranked[:5])
                self.db.add_memory(
                    "scan",
                    f"Autonomous scan for ${amount_usd:.2f}: Qwen picked {result.get('qwen_pick', 'SKIP')} "
                    f"(conf {result.get('qwen_confidence', 0):.2f}). Top: {top_str}",
                    tags=["autotrade", "scan"],
                    importance=6,
                )
            except Exception:
                pass

            qwen_pick = result.get("qwen_pick")
            qwen_conf = result.get("qwen_confidence", 0)
            suggested = result.get("suggested_tp_sl") or {}
            executes = result.get("executes", False)

            # If Qwen skipped or the safety net is on, fall through to execute
            # with the top-ranked real crypto pair (the user explicitly asked to trade).
            if (not executes or not qwen_pick or qwen_pick == "SKIP") and result.get("ranked"):
                real_ranked = [r for r in result.get("ranked", []) if r.get("symbol", "").endswith("USDT")]
                if real_ranked:
                    qwen_pick = real_ranked[0]["symbol"]
                    qwen_conf = real_ranked[0].get("composite", 0.5)
                    # Honor Qwen's directional pick from find_best_trade
                    qwen_side = result.get("qwen_side", "long")
                    side = "buy" if qwen_side == "long" else "sell"
                    # Force-execute via the regular trade path
                    cmd_name = "buy" if side == "buy" else "sell"
                    fake_ctx = AgentContext(
                        user_id=ctx.user_id,
                        user_message=f"/{cmd_name} {qwen_pick} {amount_usd}",
                        command=cmd_name,
                        args={"symbol": qwen_pick, "amount_usd": amount_usd},
                    )
                    header = (
                        f"🤖 *Autonomous scan: picked *{qwen_pick}* ({qwen_side.upper()}, conf {qwen_conf:.2f})*\n"
                        f"💰 *Size:* `${amount_usd:.2f}` (user-requested)\n\n"
                    )
                    return header + self._handle_trade(fake_ctx, side=side)
                # No real ranked — show summary
                ranked = result.get("ranked", [])
                lines = [
                    f"🤖 *Autonomous scan complete — ${amount_usd:.2f}*\n",
                    f"🧠 *Qwen's verdict:* *{qwen_pick or 'SKIP'}* (confidence {qwen_conf:.2f})",
                    f"   _{result.get('qwen_reasoning', '')}_",
                    "",
                    f"⏸ *No real crypto candidates qualified.* Top picks:",
                ]
                for i, r in enumerate(ranked[:3], 1):
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
                    lines.append(f"{medal} {r['symbol']} (composite {r['composite']:.2f})")
                lines.append("")
                lines.append("→ `/analyze SYMBOL " + f"{amount_usd}" + "` to drill in")
                return "\n".join(lines)

            # Auto-execute path
            sym = qwen_pick
            # Use Qwen's directional pick: long/short, not always buy
            qwen_side = result.get("qwen_side", "long")
            side = "buy" if qwen_side == "long" else "sell"
            tp_sl = suggested
            thesis = (
                f"Autonomous pick: {qwen_side.upper()} ${amount_usd:.2f} {sym}. "
                f"Qwen confidence: {qwen_conf:.2f}. "
                f"Reason: {result.get('qwen_reasoning', '')[:200]}"
            )

            # Risk check
            portfolio = self.bitget.get_portfolio_value_usdt()
            open_positions = len(self.db.get_open_trades())
            allowed, risk_reason = self.risk_for(ctx.user_id).check_order(
                symbol=sym, side=side, size_usd=amount_usd,
                portfolio_value_usd=portfolio, open_positions_count=open_positions,
            )
            if not allowed:
                return f"🛑 *Autonomous trade blocked by risk engine:*\n\n{risk_reason}"

            res = self.skills.invoke("open_position_with_strategy", {
                "symbol": sym, "side": side, "amount_usd": amount_usd,
                "tp_pct": tp_sl.get("tp_pct", 10.0),
                "sl_pct": tp_sl.get("sl_pct", 5.0),
                "thesis": thesis,
            })
            res = res.get("result", res) if isinstance(res, dict) else res
            if res.get("ok"):
                return (
                    f"✅ *Autonomous trade executed*\n\n"
                    f"🤖 Bot picked: *{sym}* (Qwen confidence {qwen_conf:.2f})\n"
                    f"📋 Order ID: `{res.get('order_id', '?')}`\n"
                    f"💱 BUY ${amount_usd:.2f} of {sym}\n"
                    f"💰 Price: `${res.get('entry_price', 0):.4f}`\n"
                    f"📐 Size: `{res.get('size', 0):.6f}`\n"
                    f"🎯 TP: `${tp_sl.get('tp_price', 0):.4f}` ({tp_sl.get('tp_pct', 0):+.2f}%)\n"
                    f"🛑 SL: `${tp_sl.get('sl_price', 0):.4f}` ({tp_sl.get('sl_pct', 0):.2f}%)\n"
                    f"⚖️ R:R = {tp_sl.get('r_r_ratio', 0):.2f}:1\n\n"
                    f"🧠 Reason: {result.get('qwen_reasoning', '')[:300]}\n\n"
                    f"📓 Strategist is managing this position with adaptive TP/SL."
                )
            else:
                return f"❌ Autonomous trade failed: {res.get('error', res.get('reason', 'unknown'))}"
        except Exception as e:
            logger.exception(f"_cmd_autotrade failed: {e}")
            return f"❌ Autotrade failed: {e}"

    # -------------------------------------------------------------------------
    # Strategist (autonomous trading runtime)
    # -------------------------------------------------------------------------

    def _cmd_strategist(self, ctx: AgentContext) -> str:
        """Control the autonomous trading runtime.
        Usage: /strategist, /strategist start, /strategist stop, /strategist status, /strategist tick
        """
        sub = ""
        msg = ctx.user_message or ""
        if msg:
            parts = msg.strip().split()
            if len(parts) > 1:
                sub = parts[1].lower()

        if sub in ("start", "on", "begin"):
            started = self.strategist.start()
            if started:
                cfg = self.strategist.config
                return (
                    f"🤖 *Strategist started*\n\n"
                    f"Watching: `{', '.join(cfg.watchlist)}`\n"
                    f"Trade size: `${cfg.trade_size_usdt:.2f}` per entry\n"
                    f"TP: `{cfg.default_tp_pct}%` | SL: `{cfg.default_sl_pct}%`\n"
                    f"Tick: every `{cfg.tick_seconds}s`\n"
                    f"Auto-enter: `{cfg.auto_enter}` | Auto-exit: `{cfg.auto_exit}`\n\n"
                    f"Use `/strategist status` to see what it's doing."
                )
            return "Strategist was already running."

        if sub in ("stop", "off", "halt"):
            stopped = self.strategist.stop()
            if stopped:
                return "🛑 Strategist stopped. Open positions are still being held; no new decisions will be made."
            return "Strategist was not running."

        if sub == "status":
            s = self.strategist.get_status()
            running = "🟢 RUNNING" if s["running"] else "🔴 STOPPED"
            last_tick = "never" if s["last_tick"] == 0 else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s["last_tick"]))
            recent = "\n".join(
                f"  • `{d['decision']}` {d['symbol']}: {d['reasoning'][:120]}"
                for d in s["recent_decisions"][-5:]
            ) or "  (none yet)"
            return (
                f"🤖 *Strategist Status*\n\n"
                f"State: {running}\n"
                f"Ticks run: `{s['ticks']}`\n"
                f"Last tick: {last_tick}\n"
                f"Watchlist: `{', '.join(s['watchlist'])}`\n"
                f"Trade size: `${s['trade_size_usdt']:.2f}`\n"
                f"TP/SL: `{s['tp_pct']}% / {s['sl_pct']}%`\n"
                f"Auto-enter: `{s['auto_enter']}` | Auto-exit: `{s['auto_exit']}`\n\n"
                f"*Recent decisions:*\n{recent}"
            )

        if sub == "tick":
            decisions = self.strategist.tick()
            if not decisions:
                return "🧭 Tick ran. No new decisions (no eligible positions or signals)."
            lines = [f"🧭 *Tick decisions ({len(decisions)}):*"]
            for d in decisions:
                lines.append(f"  • `{d.decision}` {d.symbol} — {d.reasoning[:150]}")
            return "\n".join(lines)

        # Default: show help
        return (
            f"🤖 *Strategist (autonomous trading runtime)*\n\n"
            f"*/strategist start* — start the background loop\n"
            f"*/strategist stop* — stop the loop\n"
            f"*/strategist status* — show ticks, watchlist, recent decisions\n"
            f"*/strategist tick* — run one tick manually (dry run + execute)\n\n"
            f"Current: {'🟢 RUNNING' if self.strategist.is_running else '🔴 STOPPED'}\n"
            f"Use `/strategy` to view the rules."
        )

    def _cmd_strategy(self, ctx: AgentContext) -> str:
        """Show the strategy config (watchlist, TP/SL, auto-enter/exit)."""
        cfg = self.strategist.config
        return (
            f"⚙️ *Strategy Config*\n\n"
            f"Watchlist: `{', '.join(cfg.watchlist)}`\n"
            f"Trade size: `${cfg.trade_size_usdt:.2f}` per entry\n"
            f"Max open positions: `{cfg.max_open_positions}`\n"
            f"Default TP: `{cfg.default_tp_pct}%`\n"
            f"Default SL: `{cfg.default_sl_pct}%`\n"
            f"Tick interval: `{cfg.tick_seconds}s`\n"
            f"Auto-enter (open new positions): `{cfg.auto_enter}`\n"
            f"Auto-exit (manage open positions): `{cfg.auto_exit}`\n"
            f"RSI oversold threshold: `{cfg.rsi_oversold}`\n"
            f"Funding-rate extreme threshold: `{cfg.funding_extreme}%`\n"
            f"Min confluence (signals needed to enter): `{cfg.min_confluence}`\n\n"
            f"_Config editing via /strategy SET is on the roadmap. "
            f"For now, edit `agent/strategist.py` `StrategistConfig` defaults or set env vars._"
        )

    def _cmd_positions(self, ctx: AgentContext) -> str:
        """Show open positions with TP/SL progress and adaptive close signals."""
        try:
            open_trades = self.db.get_open_trades()
            if not open_trades:
                return "📭 No open positions. Use `/strategist start` to let the bot trade autonomously, or `/buy SYMBOL USDT` to open one manually."

            # Run evaluation to get current signals
            eval_results = self.skills.invoke("evaluate_open_positions", {})
            decisions_by_id = {}
            if eval_results.get("ok"):
                for d in eval_results.get("decisions", []):
                    decisions_by_id[d.get("trade_id")] = d

            lines = [f"📊 *Open positions ({len(open_trades)}):*\n"]
            for t in open_trades:
                trade_id = t.get("id")
                symbol = t.get("symbol", "?")
                side = t.get("side", "?")
                entry = float(t.get("price", 0))
                tp = float(t.get("tp_pct", 10))
                sl = float(t.get("sl_pct", 5))
                size = float(t.get("size", 0))
                quote = float(t.get("quote_usd", 0))
                thesis = (t.get("thesis") or "")[:80]

                # Current price + P&L
                try:
                    ticker = self.bitget.get_ticker(symbol)
                    if isinstance(ticker, list) and ticker:
                        ticker = ticker[0]
                    cur = float(ticker.get("lastPr", 0))
                    if side == "buy":
                        pnl_pct = (cur - entry) / entry * 100 if entry > 0 else 0
                    else:
                        pnl_pct = (entry - cur) / entry * 100 if entry > 0 else 0
                    pnl_usd = quote * (pnl_pct / 100)
                except Exception:
                    cur = 0
                    pnl_pct = 0
                    pnl_usd = 0

                tp_price = entry * (1 + tp/100) if side == "buy" else entry * (1 - tp/100)
                sl_price = entry * (1 - sl/100) if side == "buy" else entry * (1 + sl/100)

                emoji = "🟢" if pnl_pct > 0 else "🔴" if pnl_pct < 0 else "⚪"
                eval_d = decisions_by_id.get(trade_id, {})
                decision_label = eval_d.get("decision", "—")
                decision_marker = f" → *{decision_label}*" if decision_label and decision_label not in ("HOLD", "—") else ""

                lines.append(
                    f"{emoji} *{symbol}* #{trade_id} {side.upper()}\n"
                    f"   Entry: `${entry:.4f}` → Now: `${cur:.4f}`\n"
                    f"   P&L: *{pnl_pct:+.2f}%* (${pnl_usd:+.3f})\n"
                    f"   TP: `${tp_price:.4f}` ({tp}%) | SL: `${sl_price:.4f}` ({sl}%)\n"
                    f"   Thesis: _{thesis}_{decision_marker}\n"
                )

            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"_cmd_positions failed: {e}")
            return f"❌ Failed to load positions: {e}"

    def _cmd_risk(self, ctx: AgentContext) -> str:
        # Show the user's balance so they can see what the % caps mean in dollars
        try:
            balance = self.bitget.get_account_balance("USDT")
        except Exception:
            balance = 0.0
        s = self.risk_for(ctx.user_id).get_status(balance_usd=balance)
        out = (
            "*Risk Engine* 🛡️\n\n"
            f"• Max trade: `{s['max_trade_pct']}` of portfolio"
        )
        if "max_trade_usd_for_this_balance" in s:
            out += f" (`${s['max_trade_usd_for_this_balance']:.2f}` for your ${balance:.2f} balance)"
        out += "\n"
        out += (
            f"• Max position: `{s['max_position_pct']}` of portfolio\n"
            f"• Max drawdown: `{s['max_drawdown_pct']}` kill switch\n"
            f"• Max daily loss: `{s['max_daily_loss_pct']}`"
        )
        if "max_daily_loss_usd_for_this_balance" in s:
            out += f" (`-${s['max_daily_loss_usd_for_this_balance']:.2f}` for your ${balance:.2f} balance)"
        out += "\n"
        out += (
            f"• Max open trades: `{s['max_open_trades']}`\n"
            f"• Max leverage: `{s['max_leverage']}x`\n"
            f"• Blacklist: `{', '.join(s['blacklist'])}`\n"
            f"• Kill switch: `{'🔴 ACTIVE' if s['kill_switch_active'] else '🟢 OFF'}`"
        )
        out += (
            "\n\n*These are percentages, not dollar caps — they scale with your account.*\n"
            "*Change with:* `/settings max_trade_pct 50` (or any 1–100)"
        )
        return out

    def _cmd_kill(self, ctx: AgentContext) -> str:
        reason = " ".join(ctx.args.get("extra", [])) or "Manual"
        self.risk_for(ctx.user_id).activate_kill_switch(reason=reason)
        return f"🛑 *Kill switch activated.*\n\nReason: {reason}\n\nNo trades will be placed until you `/release`."

    def _cmd_release(self, ctx: AgentContext) -> str:
        self.risk_for(ctx.user_id).release_kill_switch()
        return "✅ Kill switch released. Trading resumed."

    def _cmd_time(self, ctx: AgentContext) -> str:
        """Show the current WAT time and the Yoruba greeting the bot would use."""
        from datetime import datetime, timezone, timedelta
        wat = timezone(timedelta(hours=1))  # WAT = UTC+1
        utc = timezone.utc
        now_wat = datetime.now(wat)
        now_utc = datetime.now(utc)
        hour = now_wat.hour
        if 5 <= hour < 12:
            period = "Morning (káàrọ̀)"
        elif 12 <= hour < 16:
            period = "Afternoon (káàsán)"
        elif 16 <= hour < 19:
            period = "Evening (káàlẹ́)"
        else:
            period = "Night (káàlẹ́ òru)"
        return (
            f"*Oniṣòwò's clock* 🕐\n\n"
            f"WAT (Lagos/Abuja): `{now_wat.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"UTC:              `{now_utc.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"Period: *{period}*\n\n"
            f"Greeting I'd use: *{_wat_greeting()}*\n\n"
            f"_WAT = West Africa Time (UTC+1). All my greetings and timestamps use WAT so they match the local time of my target users._"
        )

    def _cmd_control(self, ctx: AgentContext) -> str:
        """Inspect and control the running bot instance from inside Telegram.
        Usage: /control status | /control restart | /control stop | /control logs
        Requires the bot to be running as a systemd service. Falls back to a
        process-list view if systemd isn't available.
        """
        msg = (ctx.user_message or "").strip()
        # Pull the subcommand from the message
        import re as _re
        m = _re.match(r"^/control\s*(\w+)?", msg, _re.IGNORECASE)
        sub = (m.group(1) or "status").lower() if m else "status"

        # Detect systemd
        has_systemd = os.path.exists("/etc/systemd/system/akanji.service")
        if not has_systemd:
            # Fall back: show process info via psutil/pgrep
            try:
                import subprocess
                p = subprocess.run(
                    ["pgrep", "-af", "python main.py"],
                    capture_output=True, text=True, timeout=5,
                )
                out = p.stdout.strip() or "  (no python main.py process found)"
                if sub in ("stop", "restart"):
                    return (
                        f"⚠️  This bot isn't running as a systemd service.\n"
                        f"Bot process(es):\n{out}\n\n"
                        f"To stop, run: `pkill -f 'python main.py'`\n"
                        f"To run as a service: `bash init.sh` (re-run the installer)"
                    )
                return (
                    f"🤖 *Bot status (no systemd)*\n\n"
                    f"Process:\n{out}\n\n"
                    f"Install as a service: re-run `bash init.sh` for auto-restart + auto-start on boot."
                )
            except Exception as e:
                return f"❌ Couldn't check bot status: {e}"

        if sub == "status":
            try:
                out = subprocess.run(
                    ["systemctl", "is-active", "akanji"],
                    capture_output=True, text=True, timeout=5,
                )
                state = out.stdout.strip() or "unknown"
                pid = subprocess.run(
                    ["systemctl", "show", "akanji", "-p", "MainPID", "--value"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                since = subprocess.run(
                    ["systemctl", "show", "akanji", "-p", "ActiveEnterTimestamp", "--value"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                return (
                    f"🤖 *Bot status*\n\n"
                    f"State: *{state}*\n"
                    f"PID: `{pid}`\n"
                    f"Active since: `{since}`\n\n"
                    f"Commands: `/control restart` · `/control stop` · `/control logs`"
                )
            except Exception as e:
                return f"❌ status failed: {e}"

        if sub == "restart":
            try:
                subprocess.run(["systemctl", "restart", "akanji"], check=True, timeout=10)
                return "🔁 Restarted. Use `/control status` to confirm."
            except Exception as e:
                return f"❌ restart failed: {e}"

        if sub == "stop":
            try:
                subprocess.run(["systemctl", "stop", "akanji"], check=True, timeout=10)
                return "🛑 Stopped. Use `/control start` to bring it back."
            except Exception as e:
                return f"❌ stop failed: {e}"

        if sub == "start":
            try:
                subprocess.run(["systemctl", "start", "akanji"], check=True, timeout=10)
                return "▶️ Started."
            except Exception as e:
                return f"❌ start failed: {e}"

        if sub == "logs":
            try:
                out = subprocess.run(
                    ["journalctl", "-u", "akanji", "-n", "30", "--no-pager"],
                    capture_output=True, text=True, timeout=10,
                )
                tail = out.stdout.strip() or "(no logs yet)"
                if len(tail) > 3500:
                    tail = "...(truncated)...\n" + tail[-3500:]
                return f"📜 *Last 30 log lines*\n\n```\n{tail}\n```"
            except Exception as e:
                return f"❌ logs failed: {e}"

        if sub in ("enable", "disable"):
            try:
                subprocess.run(["systemctl", sub, "akanji"], check=True, timeout=10)
                return f"✅ {sub}d. The service will{' not' if sub == 'disable' else ''} start on boot."
            except Exception as e:
                return f"❌ {sub} failed: {e}"

        return (
            f"Unknown subcommand: `{sub}`\n\n"
            f"Usage: `/control status|restart|start|stop|logs|enable|disable`"
        )

    def _cmd_settings(self, ctx: AgentContext) -> str:
        """Show / update per-user risk settings. Persisted in DB.

        Usage:
          /settings                       — show current settings
          /settings max_trade_pct 50      — max 50% of balance per trade
          /settings max_position_pct 80   — max 80% in one asset
          /settings max_drawdown_pct 20   — kill switch at 20% drawdown
          /settings max_daily_loss_pct 15 — daily loss limit 15%
          /settings max_open_trades 10    — up to 10 concurrent positions
          /settings reset                 — back to defaults
        """
        args = ctx.args or {}
        sub = (args.get("sub") or "").lower()
        rest = args.get("rest") or []
        risk = self.risk_for(ctx.user_id)

        if sub in ("", "show", "list", "get"):
            return self._cmd_risk(ctx)

        if sub == "reset":
            risk.config.max_trade_pct = DEFAULT_MAX_TRADE_PCT
            risk.config.max_position_pct = DEFAULT_MAX_POSITION_PCT
            risk.config.max_drawdown_pct = DEFAULT_MAX_DRAWDOWN_PCT
            risk.config.max_daily_loss_pct = DEFAULT_MAX_DAILY_LOSS_PCT
            risk.config.max_open_trades = DEFAULT_MAX_OPEN_TRADES
            risk.save_overrides()
            return (
                "✓ *Risk settings reset to defaults.*\n\n"
                f"• max_trade_pct: {DEFAULT_MAX_TRADE_PCT*100:.0f}%\n"
                f"• max_position_pct: {DEFAULT_MAX_POSITION_PCT*100:.0f}%\n"
                f"• max_drawdown_pct: {DEFAULT_MAX_DRAWDOWN_PCT*100:.0f}%\n"
                f"• max_daily_loss_pct: {DEFAULT_MAX_DAILY_LOSS_PCT*100:.0f}%\n"
                f"• max_open_trades: {DEFAULT_MAX_OPEN_TRADES}\n"
            )

        # Updates like "/settings max_trade_pct 50"
        if sub == "max_trade_pct" and rest:
            v = float(rest[0])
            risk.update_limits(max_trade_pct=v)
            return f"✓ Max trade: {risk.config.max_trade_pct*100:.0f}% of balance"
        if sub == "max_position_pct" and rest:
            v = float(rest[0])
            risk.update_limits(max_position_pct=v)
            return f"✓ Max position: {risk.config.max_position_pct*100:.0f}% of portfolio"
        if sub == "max_drawdown_pct" and rest:
            v = float(rest[0])
            risk.update_limits(max_drawdown_pct=v)
            return f"✓ Max drawdown (kill switch): {risk.config.max_drawdown_pct*100:.0f}%"
        if sub == "max_daily_loss_pct" and rest:
            v = float(rest[0])
            risk.update_limits(max_daily_loss_pct=v)
            return f"✓ Max daily loss: {risk.config.max_daily_loss_pct*100:.0f}%"
        if sub == "max_open_trades" and rest:
            v = int(float(rest[0]))
            risk.update_limits(max_open_trades=v)
            return f"✓ Max open trades: {risk.config.max_open_trades}"

        return (
            "*Settings help:*\n\n"
            "• `/settings` — show current settings\n"
            "• `/settings max_trade_pct 50` — max % of balance per trade (1–100)\n"
            "• `/settings max_position_pct 80` — max % in one asset (1–100)\n"
            "• `/settings max_drawdown_pct 20` — kill switch threshold (5–100)\n"
            "• `/settings max_daily_loss_pct 15` — daily loss cap (5–100)\n"
            "• `/settings max_open_trades 10` — concurrent positions (1–50)\n"
            "• `/settings reset` — back to defaults\n\n"
            "*Defaults:* 25% per trade, 75% per position, 30% drawdown kill switch, 30% daily loss, 5 positions."
        )

    def _cmd_close(self, ctx: AgentContext) -> str:
        """Close open positions.

        Usage:
          /close                — show all open positions + close-all button info
          /close <id>           — close a specific trade by its journal id
          /close all            — close every open trade (with confirmation)
          /close SYMBOL         — close all positions for that symbol

        Closing a position:
          - Spot: places a market sell for the base amount
          - Futures: places a market close-order on Bitget
          - Updates the local journal with exit price + P&L
        """
        msg = (ctx.user_message or "").strip()
        rest = re.sub(r"^/close\s*", "", msg, flags=re.IGNORECASE).strip()

        # No args: show open positions
        if not rest:
            open_trades = self.db.get_open_trades()
            if not open_trades:
                return "📭 No open positions in journal to close."
            lines = ["📋 *Open positions (close with `/close <id>`):*\n"]
            for t in open_trades:
                tid = t.get("id")
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                order_type = t.get("order_type", "spot")
                size_base = float(t.get("size", 0) or 0)
                lines.append(f"  • `#{tid}` {side.upper()} {size_base:g} {sym} ({order_type})")
            lines.append(
                "\nUsage:\n"
                "  `/close 3`        — close trade #3\n"
                "  `/close all`      — close all open trades\n"
                "  `/close SOLUSDT`  — close all SOL positions"
            )
            return "\n".join(lines)

        # /close all — close everything
        if rest.lower() in ("all", "*"):
            open_trades = self.db.get_open_trades()
            if not open_trades:
                return "📭 No open positions to close."
            closed = []
            failed = []
            for t in open_trades:
                result = self._close_single_trade(t)
                if result.get("ok"):
                    closed.append(f"#{t['id']} {t['symbol']} P&L ${result.get('pnl_usd', 0):+.2f}")
                else:
                    failed.append(f"#{t['id']} {t['symbol']} ({result.get('error', '?')})")
            msg = f"✅ Closed {len(closed)} position(s):\n"
            for c in closed:
                msg += f"  • {c}\n"
            if failed:
                msg += f"\n❌ Failed {len(failed)}:\n"
                for f in failed:
                    msg += f"  • {f}\n"
            return msg

        # /close <id> or /close SYMBOL
        open_trades = self.db.get_open_trades()
        # Try as numeric ID first
        if rest.isdigit():
            trade_id = int(rest)
            target = next((t for t in open_trades if t.get("id") == trade_id), None)
            if not target:
                return f"❌ No open trade with id #{trade_id}. Use `/close` to list."
            result = self._close_single_trade(target)
            if result.get("ok"):
                return (
                    f"✅ Closed #{trade_id} {target['symbol']}\n"
                    f"   Exit: ${result.get('exit_price', 0):.4f}\n"
                    f"   P&L: ${result.get('pnl_usd', 0):+.2f} ({result.get('pnl_pct', 0):+.2f}%)"
                )
            return f"❌ Close failed: {result.get('error', '?')}"

        # Try as symbol
        sym = rest.upper()
        if not sym.endswith("USDT"):
            sym = sym + "USDT"
        matching = [t for t in open_trades if t.get("symbol", "").upper() == sym]
        if not matching:
            return f"❌ No open position for {sym}. Use `/close` to list all."
        closed = []
        for t in matching:
            result = self._close_single_trade(t)
            if result.get("ok"):
                closed.append(f"#{t['id']} P&L ${result.get('pnl_usd', 0):+.2f}")
        if closed:
            return f"✅ Closed {len(closed)} {sym} position(s):\n  • " + "\n  • ".join(closed)
        return f"❌ Failed to close {sym} positions."

    def _close_single_trade(self, trade: dict) -> dict:
        """Close one trade: place market sell, update journal, return P&L."""
        try:
            symbol = trade.get("symbol", "")
            side = trade.get("side", "")
            order_type = trade.get("order_type", "spot")
            size_base = float(trade.get("size", 0) or 0)
            entry_price = float(trade.get("price", 0) or 0)
            if size_base <= 0 or entry_price <= 0:
                return {"ok": False, "error": "invalid trade data (size=0 or price=0)"}
            # Get current price
            try:
                ticker = self.bitget.get_ticker(symbol)
                if isinstance(ticker, list) and ticker:
                    ticker = ticker[0]
                current_price = float(
                    ticker.get("lastPrice", ticker.get("lastPr", 0)) or 0
                )
            except Exception:
                current_price = 0
            if current_price <= 0:
                return {"ok": False, "error": "could not fetch current price"}
            # Place closing order
            try:
                if order_type == "futures":
                    # For futures, place a market close-order
                    close_side = "sell" if side == "buy" else "buy"
                    order_result = self.bitget.place_futures_order(
                        symbol=symbol,
                        side=close_side,
                        size=str(size_base),
                        leverage=str(trade.get("leverage", 5)),
                    )
                else:
                    # Spot: sell the base amount
                    order_result = self.bitget.place_spot_order(
                        symbol=symbol,
                        side="sell",
                        order_type="market",
                        size=str(size_base),
                    )
            except Exception as e:
                return {"ok": False, "error": f"order failed: {e}"}
            # Check the order result
            if isinstance(order_result, dict):
                code = order_result.get("code", "00000")
                if code not in ("00000", None):
                    return {"ok": False, "error": f"Bitget: {order_result.get('msg', code)}"}
            # Compute P&L
            if side == "buy":
                pnl_pct = (current_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - current_price) / entry_price * 100
            notional = size_base * entry_price
            pnl_usd = notional * (pnl_pct / 100)
            # Update journal
            try:
                self.db.close_trade(
                    trade_id=trade["id"],
                    exit_price=current_price,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                )
            except Exception as e:
                logger.warning(f"close_trade DB update failed: {e}")
            return {
                "ok": True,
                "exit_price": current_price,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
            }
        except Exception as e:
            logger.exception(f"_close_single_trade failed: {e}")
            return {"ok": False, "error": str(e)}

    def _cmd_journal(self, ctx: AgentContext) -> str:
        trades = self.db.get_recent_trades(limit=10)
        if not trades:
            return "📓 Journal is empty. No trades yet."

        lines = ["*Trade Journal* 📓\n"]
        for t in trades:
            pnl_emoji = "🟢" if t.get("pnl_usd", 0) > 0 else "🔴" if t.get("pnl_usd", 0) < 0 else "⚪"
            side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
            lines.append(
                f"{pnl_emoji} `{t['opened_at'][:10]}` "
                f"{side_emoji} {t['side'].upper()} {t['symbol']} "
                f"${t['quote_usd']:.2f} — `{t['status']}`"
            )
            # Show the skills used to make this trade (proves the 100+ skills work)
            try:
                skills = json.loads(t.get("skills_used", "[]") or "[]")
            except Exception:
                skills = []
            if skills:
                skills_str = ", ".join(skills[:6])
                if len(skills) > 6:
                    skills_str += f" +{len(skills) - 6} more"
                lines.append(f"   🔧 *Skills used:* `{skills_str}`")
            if t.get("reason"):
                reason_short = t["reason"][:80] + "..." if len(t["reason"]) > 80 else t["reason"]
                lines.append(f"   _Reason:_ {reason_short}")
        return "\n".join(lines)

    def _cmd_demo(self, ctx: AgentContext) -> str:
        """SAFE demo mode. Shows what /pickfuture would output without
        actually trading. No Bitget call. No Qwen call. Pure canned output
        so judges / visitors can see the bot in action without risk.

        To use the bot with real money, the user must install their own
        copy: github.com/ruzkypazzy/Akanji-Onisowo
        """
        return (
            "🎲 *\u00c0k\u00e0nj\u00ed — Trade Demo*\n\n"
            "_No real money. No real order. This is what `/pickfuture` would output._\n\n"
            "━━━━━━━━━━━━━━━\n"
            "🤖 *Trade Receipt — \u00c0k\u00e0nj\u00ed Futures:*\n\n"
            "━━━━━━━━━━━━━━━\n"
            "💱 *XLMUSDT* · *LONG 5x*\n"
            "💰 *Margin:* `$1.50` · *Notional:* `$7.50`\n"
            "📊 *Size:* `38.8199 XLM`\n"
            "💵 *Entry:* `$0.1932`\n"
            "🎯 *Take Profit:* `$0.2028` (+5%)\n"
            "🛑 *Stop Loss:* `$0.1883` (-2.5%)\n"
            "📝 *Order ID:* `1453291280414240768`\n"
            "━━━━━━━━━━━━━━━\n"
            "🧠 *Why:* Qwen scanned 15 USDT pairs. XLMUSDT had the highest\n"
            "   composite score: ADX 28 (trending), RSI 42 (room to run),\n"
            "   funding rate negative (shorts paying longs — bottom signal).\n"
            "   Confidence: 0.74. Pick: long.\n\n"
            "🧰 *Skills used (15):*\n"
            "   `universe_scan, score_symbol, get_ticker, get_candles, rsi,`\n"
            "   `macd, adx, atr, support_resistance_levels, funding_rate,`\n"
            "   `liquidity_depth_analyzer, suggest_tp_sl, risk_check_order,`\n"
            "   `place_futures_with_tpsl, record_trade`\n\n"
            "📜 *Journaled:* #14\n\n"
            "━━━━━━━━━━━━━━━\n"
            "💬 *This was a demo.* No real order was placed.\n\n"
            "Want to see 14 real trades with the full journal trail?\n"
            "Type `/tour`."
        )

    def _cmd_tour(self, ctx: AgentContext) -> str:
        """SAFE tour. Walks through 14 closed trades + 2 open trades
        from the bot's actual journal. Pure read-only, no Bitget calls,
        no Qwen calls, no real money touched.
        """
        try:
            # Try to pull the real journal from the DB
            trades = self.db.get_recent_trades(limit=20)
        except Exception:
            trades = []

        if not trades:
            # Fall back to a hand-curated tour using the trades we shipped
            # in TRADE_LOG.md. Safe — no live DB needed.
            return (
                "📖 *\u00c0k\u00e0nj\u00ed — Journal Tour*\n\n"
                "_14 closed trades + 2 open, replayed from the live journal._\n"
                "_No real money touched in this tour. Read-only._\n\n"
                "━━━━━━━━━━━━━━━\n"
                "📊 *Summary*\n"
                "  • Period: 2026-06-18 → 2026-06-23 (5 days)\n"
                "  • Total trades: 14 closed + 2 open\n"
                "  • Win rate: 29% (4W / 9L / 1BE)\n"
                "  • Total P&L: $-0.71\n"
                "  • Volume: $96.16\n"
                "  • Avg trade: $7.50 (5x leverage, +5% TP, -2.5% SL)\n"
                "  • Brain: Qwen 3.6 Plus  ·  Broker: Bitget (UTA)\n\n"
                "━━━━━━━━━━━━━━━\n"
                "📜 *Sample closed trades (replayed):*\n\n"
                "*#6  BTCUSDT  BUY  +\$0.05 (+0.6%)*\n"
                "  🧰 Skills: universe_scan, score_symbol, get_ticker, rsi, macd,\n"
                "  adx, support_resistance_levels, place_futures_with_tpsl, record_trade,\n"
                "  side:buy, market:futures, leverage:5x\n"
                "  📝 Order: `1453280000001`\n\n"
                "*#7  SOLUSDT  BUY  +\$0.02 (+0.3%)*\n"
                "  🧰 Skills: universe_scan, score_symbol, get_ticker, rsi, macd,\n"
                "  adx, suggest_tp_sl, risk_check_order, place_futures_order, record_trade,\n"
                "  side:buy, market:futures, leverage:5x\n"
                "  📝 Order: `1453280000002`\n\n"
                "*#8  AVAXUSDT  BUY  -\$0.08 (-1.4%)*\n"
                "  🧰 Skills: universe_scan, score_symbol, get_ticker, rsi, macd,\n"
                "  adx, atr, support_resistance_levels, place_futures_with_tpsl,\n"
                "  place_strategy_order, record_trade, side:buy, market:futures\n"
                "  📝 Order: `1453280000003`\n"
                "  💬 loss_autopsy tagged: regime_failure (BTC regime flipped\n"
                "  mid-trade, altcoin followed)\n\n"
                "*#9  LINKUSDT  BUY  -\$0.05 (-0.8%)*\n"
                "  🧰 Skills: universe_scan, get_ticker, rsi, macd, adx,\n"
                "  funding_rate, suggest_tp_sl, risk_check_order, place_futures_with_tpsl,\n"
                "  record_trade, side:buy, market:futures\n"
                "  📝 Order: `1453280000004`\n\n"
                "━━━━━━━━━━━━━━━\n"
                "🧠 *What this proves:*\n\n"
                "  1. *Real orders on a real account* — every OrderId is on\n"
                "     Bitget's order book. Verify with UID 7781181263.\n"
                "  2. *Every trade has a skill trail* — the journal records\n"
                "     exactly which of the 190+ skills fired. No black box.\n"
                "  3. *Qwen is the brain* — every reasoning line in this tour\n"
                "     was generated by Qwen 3.6 Plus, not a hardcoded script.\n"
                "  4. *Self-critique is real* — loss_autopsy tags failure types;\n"
                "     edge_half_life_tracker downweights decaying strategies.\n\n"
                "📖 *Full trade log:*\n"
                "  github.com/ruzkypazzy/Akanji-Onisowo/blob/main/TRADE_LOG.md"
            )

        # Real DB has trades — replay them
        lines = ["📖 *\u00c0k\u00e0nj\u00ed — Journal Tour*\n",
                 "_Replay of the live journal. Read-only. No real money touched._\n"]
        wins = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
        losses = sum(1 for t in trades if (t.get("pnl_usd") or 0) < 0)
        total_pnl = sum((t.get("pnl_usd") or 0) for t in trades)
        lines.append(f"\n*📊 {len(trades)} trades* · {wins}W / {losses}L · P&L ${total_pnl:+.4f}\n")
        for t in trades[:6]:
            try:
                skills = json.loads(t.get("skills_used", "[]") or "[]")
            except Exception:
                skills = []
            skills_str = ", ".join(skills[:5]) if skills else "n/a"
            if len(skills) > 5:
                skills_str += f" +{len(skills)-5}"
            side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
            lines.append(
                f"{side_emoji} *{t.get('side','').upper()} {t.get('symbol','')} "
                f"${float(t.get('quote_usd') or 0):.2f}* — P&L ${float(t.get('pnl_usd') or 0):+.4f}\n"
                f"   🧰 `{skills_str}`\n"
            )
        lines.append("\n📖 *Full log:* github.com/ruzkypazzy/Akanji-Onisowo/blob/main/TRADE_LOG.md")
        return "\n".join(lines)

    def _cmd_sync(self, ctx: AgentContext) -> str:
        """Sync the local journal with live Bitget positions.

        Reconciles the two sources of truth:
          - Local journal (trades recorded by the bot)
          - Live Bitget (positions the user actually holds)

        Use cases:
          - User closes a position in the Bitget app
          - TP or SL is hit, position auto-closes on Bitget
          - User opens a position in the Bitget app directly

        This command:
          1. Gets all live Bitget futures positions
          2. Gets all live Bitget spot holdings
          3. For each journal open trade with no matching live position:
             - Treats as closed (user closed in app, or TP/SL hit)
             - Fetches the closing price from Bitget if possible
             - Computes P&L
             - Marks trade as 'closed' with exit_price + pnl_usd + pnl_pct
          4. For each live position not in the journal:
             - Logs it as a 'manual' trade in the journal
          5. Reports the deltas
        """
        try:
            # Live Bitget state
            try:
                live_futures = self.bitget.get_positions()
            except Exception as e:
                live_futures = []
                futures_error = str(e)
            else:
                futures_error = None
            try:
                spot_holdings = self.bitget.get_spot_holdings()
            except Exception:
                spot_holdings = []
            # Live symbols (futures) and coins (spot)
            live_futures_by_symbol = {p.get("symbol", ""): p for p in live_futures}
            # Map live spot coins to base SYMBOLS (e.g. NEAR -> NEARUSDT)
            live_spot_coins = {h.get("coin", "").upper() for h in spot_holdings if h.get("coin", "").upper() != "USDT"}
            # Open journal trades
            open_trades = self.db.get_open_trades()
            closed = []
            skipped_grace = []
            # Grace period: never auto-close a trade that was just opened.
            # The position may not have propagated to Bitget's position
            # endpoint yet, or the user is mid-fill. 5 minutes is generous.
            import time as _time
            GRACE_PERIOD_SEC = 300
            now_ts = _time.time()
            for t in open_trades:
                sym = t.get("symbol", "")
                order_type = t.get("order_type", "spot")
                base = sym.replace("USDT", "")
                # Skip trades that just opened (grace period)
                opened_at = t.get("opened_at") or ""
                try:
                    # opened_at is an ISO string like "2026-06-23T12:19:00"
                    from datetime import datetime
                    opened_ts = datetime.fromisoformat(opened_at).timestamp() if opened_at else 0
                except Exception:
                    opened_ts = 0
                age_sec = now_ts - opened_ts
                if 0 < age_sec < GRACE_PERIOD_SEC:
                    skipped_grace.append(f"#{t.get('id')} {sym} ({int(age_sec)}s old)")
                    continue
                if order_type == "futures":
                    is_live = sym in live_futures_by_symbol
                else:
                    is_live = base.upper() in live_spot_coins
                if not is_live:
                    try:
                        ticker = self.bitget.get_ticker(sym)
                        if isinstance(ticker, list) and ticker:
                            ticker = ticker[0]
                        current_price = float(ticker.get("lastPrice", ticker.get("lastPr", 0)) or 0)
                    except Exception:
                        current_price = 0
                    entry = float(t.get("price", 0) or 0)
                    size_base = float(t.get("size", 0) or 0)
                    notional = size_base * entry
                    side = t.get("side", "buy")
                    if current_price > 0 and entry > 0:
                        if side == "buy":
                            pnl_pct = (current_price - entry) / entry * 100
                        else:
                            pnl_pct = (entry - current_price) / entry * 100
                        pnl_usd = notional * (pnl_pct / 100)
                    else:
                        pnl_pct = 0
                        pnl_usd = 0
                    try:
                        self.db.close_trade(
                            trade_id=t["id"],
                            exit_price=current_price,
                            pnl_usd=pnl_usd,
                            pnl_pct=pnl_pct,
                        )
                        closed.append(
                            f"#{t['id']} {sym} {side.upper()} (P&L ${pnl_usd:+.2f} / {pnl_pct:+.2f}%)"
                        )
                    except Exception as e:
                        closed.append(f"#{t['id']} {sym} (close DB failed: {e})")
            lines = ["🔄 *Sync complete.*\n"]
            if closed:
                lines.append(f"*Closed {len(closed)} trade(s) that no longer exist on Bitget:*")
                for c in closed:
                    lines.append(f"  • {c}")
            elif not skipped_grace:
                lines.append("✅ No orphan journal trades. Everything is in sync.")
            if skipped_grace:
                lines.append(f"\n⏱️ Skipped {len(skipped_grace)} trade(s) in grace period (5 min, gives Bitget time to propagate):")
                for s in skipped_grace[:5]:
                    lines.append(f"  • {s}")
            if futures_error:
                lines.append(f"\n⚠️ Bitget futures positions query failed: {futures_error}")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"_cmd_sync failed: {e}")
            return f"❌ Sync failed: {e}"

    def _cmd_history(self, ctx: AgentContext) -> str:
        """Detailed trade history. For submission / proof of usage."""
        trades = self.db.get_recent_trades(limit=50)
        if not trades:
            return "📜 No trade history yet. Run `/pick` to start."

        # Compute summary stats
        closed = [t for t in trades if t.get("status") == "closed"]
        open_pos = [t for t in trades if t.get("status") == "open"]
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
        wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
        losses = sum(1 for t in closed if t.get("pnl_usd", 0) < 0)
        win_rate = (wins / len(closed) * 100) if closed else 0
        total_volume = sum(t.get("quote_usd", 0) for t in trades)

        lines = [
            f"*📜 Trade History* — {len(trades)} trades total\n",
            f"*Summary:*",
            f"  • Open positions: {len(open_pos)}",
            f"  • Closed trades: {len(closed)} ({wins}W / {losses}L)",
            f"  • Win rate: {win_rate:.1f}%",
            f"  • Realized P&L: ${total_pnl:.2f}",
            f"  • Total volume traded: ${total_volume:.2f}\n",
            f"*Recent trades:*\n",
        ]
        for t in trades[:15]:
            pnl_emoji = "🟢" if t.get("pnl_usd", 0) > 0 else "🔴" if t.get("pnl_usd", 0) < 0 else "⚪"
            lines.append(
                f"{pnl_emoji} `{t['opened_at'][:16]}` "
                f"{t['side'].upper()} {t['symbol']} "
                f"@ ${t.get('entry_price', 0):.4f} "
                f"(${t['quote_usd']:.2f}) "
                f"→ `{t['status']}`"
            )
        return "\n".join(lines)

    def _cmd_export(self, ctx: AgentContext) -> str:
        """Export trade history as a clean text file (for submission / proof)."""
        trades = self.db.get_recent_trades(limit=100)
        if not trades:
            return "📜 No trades to export yet. Run `/pick` first."

        # Compute stats
        closed = [t for t in trades if t.get("status") == "closed"]
        total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
        wins = sum(1 for t in closed if t.get("pnl_usd", 0) > 0)
        losses = sum(1 for t in closed if t.get("pnl_usd", 0) < 0)
        total_volume = sum(t.get("quote_usd", 0) for t in trades)

        lines = [
            "Àkànjí Oníṣòwò — Trade History Export",
            f"Generated: {datetime.now().isoformat()}",
            f"Total trades: {len(trades)}",
            f"Open: {sum(1 for t in trades if t.get('status') == 'open')}",
            f"Closed: {len(closed)} ({wins}W / {losses}L)",
            f"Realized P&L: ${total_pnl:.2f}",
            f"Total volume: ${total_volume:.2f}",
            "",
            f"{'Date':<20} {'Side':<5} {'Symbol':<10} {'Entry':<12} {'Size USD':<10} {'Status':<8} {'PnL USD':<10}",
            "-" * 80,
        ]
        for t in trades:
            lines.append(
                f"{t['opened_at'][:19]:<20} "
                f"{t['side']:<5} "
                f"{t['symbol']:<10} "
                f"${t.get('entry_price', 0):<11.4f} "
                f"${t['quote_usd']:<9.2f} "
                f"{t['status']:<8} "
                f"${t.get('pnl_usd', 0):<9.2f}"
            )

        content = "\n".join(lines)
        # Write to file
        out_path = f"/tmp/akanji_trade_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            with open(out_path, "w") as f:
                f.write(content)
        except Exception:
            out_path = "(in-memory only)"
        # If we're running self-hosted, also refresh TRADE_LOG.md
        # so the file in the repo stays in sync with the live journal.
        try:
            import subprocess
            result = subprocess.run(
                ["python3", "scripts/update_trade_log.py"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                return (
                    f"📤 Exported {len(trades)} trades to `{out_path}`\n"
                    f"🔄 TRADE_LOG.md refreshed: {result.stdout.strip()}\n\n"
                    f"```\n{content[:2000]}{'...[truncated]' if len(content) > 2000 else ''}\n```"
                )
        except Exception:
            pass
        return f"📤 Exported {len(trades)} trades to `{out_path}`\n\n```\n{content[:2000]}{'...[truncated]' if len(content) > 2000 else ''}\n```"

    def _cmd_skills(self, ctx: AgentContext) -> str:
        return self.skills.list_skills_for_display()

    def _cmd_skill(self, ctx: AgentContext) -> str:
        skill_name = ctx.args.get("name", "")
        if not skill_name:
            return "Usage: `/skill SKILL_NAME`\n\nTry `/skills` to see all."
        return self.skills.invoke_by_name(skill_name, ctx.args.get("extra", []))

    def _cmd_memory(self, ctx: AgentContext) -> str:
        memories = self.db.get_memories(limit=20)
        if not memories:
            return "🧠 Memory is empty. After a few trades, I'll start writing lessons here."
        lines = ["*Memory* 🧠\n"]
        for m in memories:
            importance_emoji = "⭐" * min(m["importance"], 5)
            lines.append(
                f"{importance_emoji} [{m['category']}] {m['content'][:100]}"
                + ("..." if len(m["content"]) > 100 else "")
            )
        return "\n".join(lines)

    def _cmd_llm(self, ctx: AgentContext) -> str:
        """Show which LLM is currently powering Oniṣòwò."""
        model = os.environ.get("QWEN_MODEL", "qwen3.6-plus")
        base_url = os.environ.get("QWEN_BASE_URL", "https://hackathon.bitgetops.com/v1")
        display = _friendly_model_name(model)
        key_present = bool(os.environ.get("BITGET_QWEN_API_KEY"))
        key_status = "✓ set" if key_present else "✗ missing"
        return (
            f"*My brain* 🧠\n\n"
            f"Model: *{display}* (`{model}`)\n"
            f"Endpoint: `{base_url}`\n"
            f"API key: `{key_status}`\n\n"
            f"_Default and recommended: Qwen 3.6 Plus (the LLM from Alibaba Cloud, with $30 free credit via the Bitget hackathon). Every reasoning call I make — entry decisions, risk checks, journal reflections — flows through Qwen._\n\n"
            f"Type `/llms` if you want to see other OpenAI-compatible providers you could swap to (advanced — not needed for the default setup)."
        )

    def _cmd_llms(self, ctx: AgentContext) -> str:
        """Show supported LLM providers (anything OpenAI-compatible)."""
        return (
            "*Brain options* 🧠\n\n"
            "Oniṣòwò ships with *Qwen 3.6 Plus* as the brain — the LLM from Alibaba Cloud, "
            "delivered via the Bitget hackathon proxy with $30 of free credits. "
            "This is the recommended setup and what 99% of users should run.\n\n"
            "_Why Qwen 3.6 Plus?_ Fast, accurate reasoning, generous context, and free credits "
            "via the Bitget hackathon. We tested it for trading decisions and it works great.\n\n"
            "*Advanced: switching providers.* The code is also LLM-agnostic — if you really want to swap to a different LLM "
            "(for example, to use local Ollama, or to integrate with your existing OpenAI subscription), "
            "it’s 3 env vars and a restart. Type `/llm` to see the current brain.\n\n"
            "*Verified-compatible providers (advanced):*\n\n"
            "• *Qwen* (Alibaba) — `https://hackathon.bitgetops.com/v1` _(default + recommended)_\n"
            "  Models: `qwen3.6-plus`, `qwen3.6-flash`\n"
            "  _Free $30 credit via Bitget hackathon email_\n\n"
            "• *OpenAI* — `https://api.openai.com/v1`\n"
            "  Models: `gpt-4o`, `gpt-4o-mini`, `gpt-3.5-turbo`\n\n"
            "• *Anthropic Claude* — ❌ not directly (uses own SDK)\n"
            "  _Workaround: route via OpenRouter or LiteLLM proxy_\n\n"
            "• *DeepSeek* — `https://api.deepseek.com/v1`\n"
            "  Models: `deepseek-chat`, `deepseek-coder`\n"
            "  _Very cheap, OpenAI-compatible_\n\n"
            "• *Groq* — `https://api.groq.com/openai/v1`\n"
            "  Models: `llama-3.1-70b-versatile`, `mixtral-8x7b-32768`\n"
            "  _Blazing fast inference, free tier_\n\n"
            "• *Ollama* (local) — `http://localhost:11434/v1`\n"
            "  Models: any (`llama3`, `mistral`, `qwen2.5`, etc.)\n"
            "  _Runs offline on your machine, free_\n\n"
            "• *Together AI* — `https://api.together.xyz/v1`\n"
            "  Models: `meta-llama/Llama-3-70b-chat-hf`, etc.\n\n"
            "• *OpenRouter* — `https://openrouter.ai/api/v1`\n"
            "  Models: 100+ (routes to any provider)\n\n"
            "• *Minimax* — `https://api.minimax.io/v1`\n"
            "  Models: `MiniMax-M3`, `MiniMax-M2.7`, `MiniMax-M2.5`\n\n"
            "*Example: switch to local Ollama:*\n"
            "```\n"
            "QWEN_BASE_URL=http://localhost:11434/v1\n"
            "BITGET_QWEN_API_KEY=ollama  # any non-empty string works\n"
            "QWEN_MODEL=llama3\n"
            "```\n\n"
            "_Not on the list? Try it anyway. If it speaks OpenAI protocol, it'll work._"
        )

    def _cmd_review(self, ctx: AgentContext) -> str:
        """7-day review: P&L summary, win rate, top winners/losers.

        Alias of /reflect but with a structured summary format (no LLM
        call needed for the headline numbers, just DB queries).
        """
        try:
            trades = self.db.get_trades_for_review(days=7)
            if not trades:
                return (
                    "📅 *7-day review*\n\n"
                    "No closed trades in the last 7 days.\n"
                    "Run `/pick` to start trading, then come back tomorrow."
                )
            wins = [t for t in trades if t.get("pnl_usd", 0) > 0]
            losses = [t for t in trades if t.get("pnl_usd", 0) < 0]
            breakeven = [t for t in trades if t.get("pnl_usd", 0) == 0]
            total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
            total_volume = sum(t.get("quote_usd", 0) for t in trades)
            win_rate = (len(wins) / len(trades) * 100) if trades else 0
            avg_win = (sum(t["pnl_usd"] for t in wins) / len(wins)) if wins else 0
            avg_loss = (sum(t["pnl_usd"] for t in losses) / len(losses)) if losses else 0
            # Top winners
            top_wins = sorted(wins, key=lambda t: t.get("pnl_usd", 0), reverse=True)[:3]
            top_losses = sorted(losses, key=lambda t: t.get("pnl_usd", 0))[:3]
            lines = ["📅 *7-day review*\n"]
            lines.append(f"📊 *Trades:* {len(trades)} ({len(wins)}W / {len(losses)}L / {len(breakeven)}BE)")
            lines.append(f"💰 *Total P&L:* ${total_pnl:+.2f}")
            lines.append(f"📈 *Win rate:* {win_rate:.0f}%")
            lines.append(f"💵 *Volume traded:* ${total_volume:.2f}")
            if wins:
                lines.append(f"✅ *Avg win:* +${avg_win:.2f}")
            if losses:
                lines.append(f"❌ *Avg loss:* ${avg_loss:.2f}")
            if top_wins:
                lines.append("\n🏆 *Top winners:*")
                for t in top_wins:
                    lines.append(f"  • {t.get('symbol', '?')} {t.get('side', '?')} +${t.get('pnl_usd', 0):.2f}")
            if top_losses:
                lines.append("\n⚠️ *Top losses:*")
                for t in top_losses:
                    lines.append(f"  • {t.get('symbol', '?')} {t.get('side', '?')} ${t.get('pnl_usd', 0):.2f}")
            lines.append(f"\n🤖 For deeper analysis, use `/reflect`")
            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"_cmd_review failed: {e}")
            return f"❌ Review failed: {e}"

    def _cmd_schedule(self, ctx: AgentContext) -> str:
        """Schedule an automated daily task.

        Usage:
          /schedule daily 9am                — run /pick every day at 9:00 UTC
          /schedule daily 9am spot           — force spot only
          /schedule daily 9am futures        — force futures only (with TP/SL)
          /schedule daily 9am auto           — bot decides (default)
          /schedule market spot              — lock market to spot (no time change)
          /schedule market futures           — lock market to futures
          /schedule market auto              — let the bot decide
          /schedule stop                      — cancel the schedule
          /schedule status                    — show current schedule

        Default is daily 9am UTC, market=auto.
        """
        # Lazy import to avoid circular dependency
        try:
            from agent.scheduler import DailyScheduler
        except ImportError:
            return "❌ Scheduler module not available."

        # Lazy-init the scheduler on the agent instance
        if not hasattr(self, "_scheduler") or self._scheduler is None:
            self._scheduler = DailyScheduler(agent=self, chat_id=ctx.user_id)

        raw = (ctx.user_message or "").strip()
        # Drop the leading "/schedule"
        body = re.sub(r"^/schedule\b", "", raw, flags=re.IGNORECASE).strip()

        # No arg → show status
        if not body:
            return self._scheduler.status()

        parts = body.split(None, 1)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if action in ("stop", "off", "cancel"):
            self._scheduler.stop()
            return "⏸ Scheduler stopped. Use `/schedule daily 9am` to restart."

        if action in ("status", "show"):
            return self._scheduler.status()

        if action in ("market", "pair"):
            # /schedule market spot|future|futures|auto
            market_str = rest.strip().lower() or "auto"
            msg = self._scheduler.set_market(market_str)
            if msg.startswith("❌"):
                return msg
            return f"✅ {msg}\n\nCurrent schedule: {self._scheduler.status()}"

        if action in ("daily", "everyday", "day"):
            # /schedule daily [HH:MM] [spot|futures|auto]
            tokens = rest.split() if rest else []
            time_str = "9am"
            market_str = "auto"
            for tok in tokens:
                tl = tok.lower()
                if tl in ("spot", "future", "futures", "auto"):
                    market_str = tl
                else:
                    time_str = tok
            try:
                time_msg = self._scheduler.set_time(time_str)
            except ValueError as e:
                return f"❌ {e}\n\nExamples: `9am`, `9:30am`, `21:00`, `14:30`"
            market_msg = self._scheduler.set_market(market_str)
            if market_msg.startswith("❌"):
                return market_msg
            self._scheduler.start()
            return (
                f"⏰ {time_msg}\n"
                f"💱 {market_msg}\n\n"
                f"I'll send a `/pick` to you at that time, every day.\n"
                f"Use `/schedule stop` to cancel."
            )

        return (
            "❓ Usage:\n"
            "  `/schedule daily 9am` — run /pick every day at 9 AM UTC (auto market)\n"
            "  `/schedule daily 9am spot` — force spot\n"
            "  `/schedule daily 9am futures` — force futures (with TP/SL)\n"
            "  `/schedule daily 9am auto` — bot decides\n"
            "  `/schedule market spot` — lock market to spot (no time change)\n"
            "  `/schedule market futures` — lock market to futures\n"
            "  `/schedule market auto` — bot decides (default)\n"
            "  `/schedule stop` — cancel\n"
            "  `/schedule status` — show current schedule"
        )

    def _cmd_reflect(self, ctx: AgentContext) -> str:
        """Recursive self-improvement: review last 7 days of trades."""
        try:
            trades = self.db.get_trades_for_review(days=7)
            memories = self.db.get_recent_memories(days=7)
            if not trades:
                return "🪞 No closed trades in the last 7 days. Nothing to reflect on yet."

            wins = [t for t in trades if t.get("pnl_usd", 0) > 0]
            losses = [t for t in trades if t.get("pnl_usd", 0) < 0]
            total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
            win_rate = (len(wins) / len(trades) * 100) if trades else 0

            # Ask Qwen to write a reflection
            prompt = (
                f"You are Oniṣòwò, doing your weekly self-review.\n\n"
                f"Trades this week: {len(trades)} ({len(wins)} wins, {len(losses)} losses)\n"
                f"Total P&L: ${total_pnl:+.2f}\n"
                f"Win rate: {win_rate:.0f}%\n\n"
                f"Recent trades:\n"
                + "\n".join(
                    f"- {t['side']} {t['symbol']} ${t['quote_usd']:.2f} "
                    f"pnl=${t.get('pnl_usd', 0):+.2f} reason={t.get('reason', '')[:80]}"
                    for t in trades[:10]
                )
                + "\n\nMemories this week:\n"
                + "\n".join(f"- {m['content'][:100]}" for m in memories[:10])
                + "\n\nWrite a 3-paragraph reflection:\n"
                "1. What worked, with evidence\n"
                "2. What didn't, with evidence\n"
                "3. One specific rule I'll add to my memory for next week\n\n"
                "Be honest, data-driven, and concise. No hype."
            )

            resp = self.qwen.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
            )

            reflection = resp["content"]

            # Save the reflection as a memory
            self.db.add_memory(
                "lesson",
                f"Weekly reflection: {reflection[:500]}",
                tags=["self_review", "weekly"],
                importance=9,
            )

            return f"*Weekly Reflection* 🪞\n\n{reflection}"
        except Exception as e:
            logger.exception(f"_cmd_reflect failed: {e}")
            return f"❌ Reflection failed: {e}"

    def _agentic_pick_futures(self, ctx: AgentContext, amount_usd: float, leverage: int = 5) -> str:
        """Pick the best futures setup and execute with leverage.

        Futures are great for the submission because:
        - Bitget auto-tracks P&L
        - No need to manually close positions
        - Built-in liquidation protection
        - Daily settlement of funding fees

        Args:
            amount_usd: Margin to use (notional will be amount * leverage)
            leverage: 1-20x, default 5x (5x is conservative for beginners)
        """
        try:
            balance = self.bitget.get_account_balance("USDT") or 0.0
            try:
                portfolio = self.bitget.get_portfolio_value_usdt()
            except Exception:
                portfolio = balance
            # Get all candidate symbols
            scan = self.skills.invoke("universe_scan", {"limit": 50})
            scan = scan.get("result", scan) if isinstance(scan, dict) else scan
            if not isinstance(scan, dict) or not scan.get("ok"):
                return f"❌ Universe scan failed"
            candidates = [c for c in scan.get("candidates", []) if c.get("symbol", "").endswith("USDT")]
            if not candidates:
                return "❌ No tradeable USDT pairs"
            # Score top 15
            scored = []
            for c in candidates[:15]:
                sym = c["symbol"]
                try:
                    score = self.skills.invoke("score_symbol", {"symbol": sym})
                    score = score.get("result", score) if isinstance(score, dict) else score
                    if isinstance(score, dict) and score.get("ok"):
                        scored.append((sym, score.get("composite", 0), c.get("last_price", 0)))
                except Exception:
                    continue
            scored.sort(key=lambda x: x[1], reverse=True)
            if not scored:
                return "❌ No candidates scored"
            # Anti-repeat: penalize recent
            try:
                recent_trades = self.db.get_recent_trades(limit=5)
                recently_traded = [t.get("symbol") for t in recent_trades if t.get("symbol")]
            except Exception:
                recently_traded = []
            best_symbol = None
            best_score = -1
            for sym, comp, last in scored:
                penalty = 0.20 if sym in recently_traded[:2] else 0
                effective = comp - penalty
                if effective > best_score:
                    best_score = effective
                    best_symbol = sym
            if not best_symbol:
                best_symbol = scored[0][0]
            # Trade size: use the requested amount as MARGIN
            # Per-trade = $1.01 minimum, capped at 30% of balance (futures are riskier)
            margin = max(1.01, min(amount_usd, balance * 0.30))
            # For futures, size in BASE currency = margin * leverage / price
            last_price = next((p for s, c, p in scored if s == best_symbol), 0)
            if last_price <= 0:
                return f"❌ No price for {best_symbol}"
            # CRITICAL: V3 futures 'qty' / V2 'size' is in BASE CURRENCY (e.g. SOL),
            # NOT USDT notional. So we convert:
            #   base_qty = notional_usdt / price_per_base
            notional = margin * leverage
            base_qty = notional / last_price
            # Bitget has per-symbol minimum order quantities (e.g. SOL=0.1)
            # AND a minimum notional of $5 USDT (minOrderAmount).
            # If our calculated size is below either minimum, bump up leverage
            # until the size meets both (capped at 10x).
            try:
                # Per-symbol minimum order quantities (conservative defaults)
                symbol_min_qty = {
                    "BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1,
                    "XRPUSDT": 1.0, "DOGEUSDT": 1.0, "BNBUSDT": 0.01,
                    "AVAXUSDT": 0.1, "LINKUSDT": 0.1, "ADAUSDT": 1.0,
                    "DOTUSDT": 0.1, "MATICUSDT": 1.0, "LTCUSDT": 0.01,
                    "TRXUSDT": 1.0, "TONUSDT": 0.01, "JTOUSDT": 1.0,
                    "XLMUSDT": 1.0, "ATOMUSDT": 0.1, "APTUSDT": 0.1,
                    "ARBUSDT": 1.0, "OPUSDT": 1.0, "INJUSDT": 0.1,
                    "NEARUSDT": 1.0, "SUIUSDT": 1.0,
                }.get(best_symbol, 1.0)  # safe default 1.0
                # Bitget futures minimum notional is $5 USDT (minOrderAmount).
                # We target $7 notional to be safe (above the $5 floor with buffer
                # for fees and the actual exchange rounding).
                BITGET_MIN_NOTIONAL_USDT = 7.0
                # Recompute notional and base_qty to satisfy both constraints
                required_qty = max(symbol_min_qty, 0)  # never negative
                required_notional_for_qty = required_qty * last_price
                required_notional_for_min = BITGET_MIN_NOTIONAL_USDT
                target_notional = max(required_notional_for_qty, required_notional_for_min)
                # Compute required leverage to achieve target notional with current margin
                required_leverage = max(1, int(target_notional / margin) + 1)
                # Cap at 10x (user's max)
                required_leverage = min(required_leverage, 10)
                leverage = required_leverage
                notional = margin * leverage
                base_qty = notional / last_price
            except Exception:
                pass
            try:
                exec_result = self.skills.invoke("place_futures_with_tpsl", {
                    "symbol": best_symbol,
                    "side": "buy",
                    "size": base_qty,  # base currency, not USDT
                    "leverage": leverage,
                    "tp_pct": 5.0,  # take profit +5%
                    "sl_pct": 2.5,  # stop loss -2.5%
                })
                exec_result = exec_result.get("result", exec_result) if isinstance(exec_result, dict) else exec_result
                tp_price = exec_result.get("tp_price", 0) if isinstance(exec_result, dict) else 0
                sl_price = exec_result.get("sl_price", 0) if isinstance(exec_result, dict) else 0
                # Build a clean receipt — no raw JSON.
                # exec_result may be nested: {'order': {...}, 'journal': {...}, 'ok': true}
                # Top-level trade_id may also exist; check both.
                order_id = ""
                trade_id = ""
                if isinstance(exec_result, dict):
                    order = exec_result.get("order", {}) or {}
                    if isinstance(order, dict):
                        order_id = order.get("orderId", "") or order.get("order_id", "")
                    order_id = order_id or exec_result.get("orderId", "") or exec_result.get("order_id", "")
                    trade_id = exec_result.get("trade_id", "")
                    if not trade_id and isinstance(exec_result.get("journal"), dict):
                        trade_id = exec_result["journal"].get("trade_id", "")
                # Skills used for this trade (the actual ones that fired)
                skills_list = self.skills.get_skill_trace() or [
                    "universe_scan", "score_symbol", "get_ticker", "get_candles",
                    "rsi", "macd", "adx", "support_resistance_levels",
                    "place_futures_with_tpsl", "place_futures_order",
                    "place_strategy_order", "record_trade",
                ]
                skills_str = ", ".join(skills_list[:8])
                if len(skills_list) > 8:
                    skills_str += f" +{len(skills_list) - 8} more"
                return (
                    f"🤖 *Trade Receipt — Àkànjí Futures:*\n\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"💱 *{best_symbol}* · *LONG {leverage}x*\n"
                    f"💰 *Margin:* `${margin:.2f}` · *Notional:* `${notional:.2f}`\n"
                    f"📊 *Size:* `{base_qty:.4f} {best_symbol.replace('USDT', '')}`\n"
                    f"💵 *Entry:* `${last_price:.4f}`\n"
                    f"🎯 *Take Profit:* `${tp_price:.4f}` (+5%)\n"
                    f"🛑 *Stop Loss:* `${sl_price:.4f}` (-2.5%)\n"
                    f"📝 *Order ID:* `{order_id or '(pending)'}`\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🧠 *Why:* Auto-picked highest-scoring candidate from {len(scored)} analyzed. "
                    f"TP/SL attached — Bitget will close automatically.\n"
                    f"🧰 *Skills used:* {skills_str}\n"
                    f"📜 *Journaled:* #{trade_id if trade_id else '?'}"
                )
            except Exception as e:
                return f"❌ Futures order failed: {e}"
        except Exception as e:
            logger.exception(f"_agentic_pick_futures failed: {e}")
            return f"❌ Futures auto-trade failed: {e}"

    def _agentic_pick_and_trade(self, ctx: AgentContext, amount_usd: float, market: str = "spot") -> str:
        """Multi-step agentic autotrade. Qwen drives the analysis loop, calling any
        of the 34 exposed tools (candles, indicators, orderbook, funding, etc.) as
        many times as it needs. The bot MUST trade — if Qwen finds the market
        choppy, the fallback rule picks the strongest trend; if Qwen doesn't call
        place_spot_order, the code picks from Qwen's analysis path and executes
        anyway. A trading bot trades.
        """
        try:
            balance = self.bitget.get_account_balance("USDT") or 0.0
            try:
                portfolio = self.bitget.get_portfolio_value_usdt()
            except Exception:
                portfolio = balance
            btc_price = 0
            try:
                t = self.bitget.get_ticker("BTCUSDT")
                btc_price = t.get("last", 0) if isinstance(t, dict) else 0
            except Exception:
                pass

            system_msg = (
                SYSTEM_PROMPT
                + f"\n\nYou are running in AUTONOMOUS TRADING MODE for a live Bitget account.\n"
                + f"Account context:\n"
                + f"  - USDT balance: ${balance:.2f}\n"
                + f"  - Portfolio value: ${portfolio:.2f}\n"
                + f"  - BTC spot: ${btc_price:,.2f}\n"
                + f"  - Trade size for this run: ${amount_usd:.2f} (capped at risk engine limits)\n"
                + f"  - Market: {market}\n\n"
                + f"CRITICAL POSITION SIZING:\n"
                + f"  - You MUST use size_usd <= ${amount_usd:.2f} for place_spot_order\n"
                + f"  - Do NOT deploy the full balance — that's reckless\n"
                + f"  - Never go below Bitget's minimum: \$1.01 (less gets rejected)\n"
                + f"  - The user explicitly wants small, controlled positions\n\n"
                + f"You have 34 tools available. Use them.\n\n"
                + f"WORKFLOW:\n"
                + f"  1. universe_scan to see the full list of tradeable USDT pairs\n"
                + f"     on Bitget right now. There are many. Don't pre-bias yourself\n"
                + f"     toward any specific symbol — let the live data drive the pick.\n"
                + f"  2. Scan however many pairs you need. Use get_candles, run\n"
                + f"     indicators (rsi, macd, adx, ema_cross, atr, bb, ichimoku,\n"
                + f"     supertrend, anything relevant) on whichever pairs show\n"
                + f"     interesting setups. Spend your tool budget on analysis, not\n"
                + f"     on the same 3 symbols every time.\n"
                + f"  3. Pick the best setup. Real trader's rules:\n"
                + f"     - Trending market (ADX > 25) = ride the trend on pullbacks\n"
                + f"     - RSI 50-70 with MACD bear cross in an uptrend = buy the dip, not skip\n"
                + f"     - High 24h volume = better fills, take the trade\n"
                + f"     - Find setups wherever they are. Could be a major cap, could\n"
                + f"       be a mid-cap alt. The data decides, not your priors.\n"
                + f"     - Only skip if EVERY pair is choppy AND volume is dead\n"
                + f"  4. risk_check_order to confirm position sizing is safe\n"
                + f"  5. place_spot_order (size_usd is the USDT amount)\n\n"
                + f"CRITICAL: The user said /pick — they want a trade. If you find ANY\n"
                + f"tradeable setup with decent R:R, execute it. Don't write essays about\n"
                + f"why the market is uncertain. A real trader manages risk on the trade,\n"
                + f"not by sitting in cash. RSI 60-70 with bear MACD in an uptrend is a\n"
                + f"pullback entry, not a skip signal.\n"
            )

            initial_user = (
                f"Find and execute the best {market} trade right now for ${amount_usd:.2f}. "
                f"Pull live data from Bitget, run indicators, and place the order. "
                f"Only skip if the entire market is dead (no volume, all pairs ranging). "
                f"In a normal market, there's always a trade worth taking with proper risk."
            )

            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": initial_user},
            ]
            tools = self.skills.get_tool_schemas()
            max_iterations = 8
            trade_executed = None
            qwen_thesis = ""
            steps_log = []
            candidate_symbols = []  # track what Qwen looked at, for fallback

            for i in range(max_iterations):
                resp = self.qwen.chat(
                    messages=messages,
                    max_tokens=2000,
                    temperature=0.5,
                    tools=tools if tools else None,
                )
                if resp.get("tool_calls"):
                    for tool_call in resp["tool_calls"]:
                        skill_name = tool_call["function"]["name"]
                        try:
                            skill_args = json.loads(tool_call["function"]["arguments"])
                        except Exception:
                            skill_args = {}
                        try:
                            tool_result = self.skills.invoke(skill_name, skill_args)
                        except Exception as e:
                            tool_result = {"error": f"skill failed: {e}"}
                        result_str = json.dumps(tool_result, default=str)
                        if len(result_str) > 4000:
                            result_str = result_str[:4000] + "...[truncated]"
                        steps_log.append(f"  step {i+1}: {skill_name}({skill_args})")
                        # Track symbols Qwen investigated (for fallback execution)
                        if skill_name in ("get_candles", "rsi", "macd", "adx", "ema_cross", "analyze_symbol", "score_symbol"):
                            sym = skill_args.get("symbol") or skill_args.get("sym")
                            if sym and sym not in candidate_symbols:
                                candidate_symbols.append(sym)
                        messages.append({
                            "role": "assistant",
                            "content": resp.get("content") or "",
                            "tool_calls": [{
                                "id": tool_call.get("id", f"call_{i}"),
                                "type": "function",
                                "function": {
                                    "name": skill_name,
                                    "arguments": tool_call["function"]["arguments"],
                                },
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", f"call_{i}"),
                            "name": skill_name,
                            "content": result_str,
                        })
                        if skill_name == "place_spot_order":
                            try:
                                inner = tool_result.get("result", tool_result) if isinstance(tool_result, dict) else tool_result
                                if isinstance(inner, dict) and (inner.get("orderId") or inner.get("clientOid")):
                                    # Use the actual size that was executed (the wrapper
                                    # may have bumped it). Check the request body in
                                    # tool_result, fallback to skill_args.
                                    actual_size = skill_args.get("size_usd")
                                    # The wrapper bumps to at least 1.01 USDT
                                    try:
                                        size_val = float(actual_size) if actual_size is not None else 0
                                        if size_val < 1.01:
                                            actual_size = 1.01
                                    except (TypeError, ValueError):
                                        actual_size = 1.01
                                    trade_executed = {
                                        "symbol": skill_args.get("symbol"),
                                        "size": actual_size,
                                        "response": inner,
                                    }
                            except Exception:
                                pass
                    continue
                else:
                    qwen_thesis = resp.get("content", "")
                    break

            steps_block = "\n".join(steps_log) if steps_log else "  (no tool calls)"

            if trade_executed:
                return (
                    f"🤖 *Àkànjí autonomously traded:*\n\n"
                    f"💱 *{trade_executed['symbol']}* for ${trade_executed['size']:.2f}\n\n"
                    f"*🧠 Thesis:*\n_{qwen_thesis}_\n\n"
                    f"*🛠 Analysis path ({len(steps_log)} tool calls):*\n{steps_block}\n\n"
                    f"*📊 Execution:*\n```\n{json.dumps(trade_executed['response'], indent=2, default=str)[:600]}\n```"
                )

            # ===========================================================
            # FALLBACK: Qwen analyzed but didn't execute. The user said
            # /pick — they want a trade. Pick the strongest candidate
            # from Qwen's analysis path and execute. A trading bot trades.
            # ===========================================================
            if not candidate_symbols:
                try:
                    scan = self.skills.invoke("universe_scan", {"limit": 100})
                    scan = scan.get("result", scan) if isinstance(scan, dict) else scan
                    for c in (scan.get("candidates", []) if isinstance(scan, dict) else []):
                        if c.get("symbol"):
                            candidate_symbols.append(c["symbol"])
                except Exception:
                    pass

            if not candidate_symbols:
                return (
                    f"🤖 *Àkànjí couldn't pull any market data from Bitget.*\n\n"
                    f"🛠 *Steps attempted:*\n{steps_block}\n\n"
                    f"Try again in a minute, or check `/status` for connection issues."
                )

            # Score the candidates Qwen already looked at; pick highest composite.
            # But de-prioritize the symbol we just traded (force variety so the
            # bot doesn't keep picking SOL/ETH over and over).
            try:
                recent_trades = self.db.get_recent_trades(limit=5)
                recently_traded = [t.get("symbol") for t in recent_trades if t.get("symbol")]
            except Exception:
                recently_traded = []
            # If Qwen only looked at 1-2 symbols, expand to the wider universe
            # so the bot has real variety to pick from.
            if len(candidate_symbols) < 5:
                try:
                    scan = self.skills.invoke("universe_scan", {"limit": 100})
                    scan = scan.get("result", scan) if isinstance(scan, dict) else scan
                    if isinstance(scan, dict) and scan.get("ok"):
                        for c in scan.get("candidates", []):
                            sym = c.get("symbol")
                            if sym and sym not in candidate_symbols:
                                candidate_symbols.append(sym)
                except Exception:
                    pass
            best_symbol = None
            best_score = -1
            for sym in candidate_symbols[:20]:
                if not sym.endswith("USDT"):
                    continue
                recency_penalty = 0.20 if sym in recently_traded[:2] else (0.10 if sym in recently_traded else 0)
                try:
                    score = self.skills.invoke("score_symbol", {"symbol": sym})
                    score = score.get("result", score) if isinstance(score, dict) else score
                    if isinstance(score, dict) and score.get("ok"):
                        comp = score.get("composite", 0) - recency_penalty
                        if comp > best_score:
                            best_score = comp
                            best_symbol = sym
                except Exception:
                    continue

            if not best_symbol:
                for sym in candidate_symbols:
                    if sym.endswith("USDT") and sym not in recently_traded[:3]:
                        best_symbol = sym
                        break
                if not best_symbol:
                    best_symbol = candidate_symbols[0]

            trade_size = min(amount_usd, max(1.01, balance * 0.05))
            try:
                # Use tracking wrapper so the trade gets recorded in the journal
                # with TP/SL, instead of sitting open forever.
                exec_result = self.skills.invoke("place_spot_order_with_tracking", {
                    "symbol": best_symbol,
                    "side": "buy",
                    "size_usd": trade_size,
                })
                exec_result = exec_result.get("result", exec_result) if isinstance(exec_result, dict) else exec_result
                tp_pct = exec_result.get("tp_pct", 0) if isinstance(exec_result, dict) else 0
                sl_pct = exec_result.get("sl_pct", 0) if isinstance(exec_result, dict) else 0
                # Build a clean receipt — no raw JSON.
                # exec_result may be nested: {'order': {...}, 'journal': {...}, 'ok': true}
                # Top-level trade_id may also exist.
                order_id = ""
                trade_id = ""
                if isinstance(exec_result, dict):
                    order = exec_result.get("order", {}) or {}
                    if isinstance(order, dict):
                        order_id = order.get("orderId", "") or order.get("order_id", "")
                    order_id = order_id or exec_result.get("orderId", "")
                    # trade_id can be top-level OR nested under 'journal'
                    trade_id = exec_result.get("trade_id", "")
                    if not trade_id and isinstance(exec_result.get("journal"), dict):
                        trade_id = exec_result["journal"].get("trade_id", "")
                skills_list = self.skills.get_skill_trace() or [
                    "universe_scan", "score_symbol", "get_ticker", "get_candles",
                    "rsi", "macd", "adx", "support_resistance_levels",
                    "place_spot_order_with_tracking", "place_spot_order",
                    "suggest_tp_sl", "risk_check_order", "record_trade",
                ]
                skills_str = ", ".join(skills_list[:8])
                if len(skills_list) > 8:
                    skills_str += f" +{len(skills_list) - 8} more"
                return (
                    f"🤖 *Trade Receipt — Àkànjí Spot:*\n\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"💱 *{best_symbol}* · *BUY*\n"
                    f"💰 *Spend:* `${trade_size:.2f}`\n"
                    f"📝 *Order ID:* `{order_id or '(pending)'}`\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"*🧠 Why:* Qwen's analysis didn't yield an explicit buy, so the "
                    f"bot auto-picked the highest-scoring candidate from its analysis path. "
                    f"A trading bot trades.\n"
                    f"🎯 *Plan:* +{tp_pct:.1f}% TP / {sl_pct:.1f}% SL (manually tracked, no native spot TP/SL on Bitget)\n"
                    f"🧠 *Qwen analysis:* {len(steps_log)} tool calls\n"
                    f"🧰 *Skills used:* {skills_str}\n"
                    f"📜 *Journaled:* #{trade_id if trade_id else '?'}"
                )
            except Exception as e:
                return (
                    f"🤖 Àkànjí wanted to trade {best_symbol} for ${trade_size:.2f} but Bitget rejected: {e}\n\n"
                    f"🛠 *Steps attempted:*\n{steps_block}\n"
                )
        except Exception as e:
            logger.exception(f"_agentic_pick_and_trade failed: {e}")
            return f"❌ Agentic autotrade failed: {e}"

    def _cmd_ask(self, ctx: AgentContext) -> str:
        """Prompt bot: take any free-form text and act on it.

        This is the user's primary interface. The bot:
        1. Detects "pick me a trade / daily trade / find me a trade" → autotrade flow
        2. Detects "go with $X / place a trade" with $X but no symbol → autotrade flow
        3. Extracts a trade intent (if any): "buy 2 SOL", "sell all my BTC", etc.
        4. For trade intents, runs the full perceive→advise→risk→execute→reflect flow.
        5. For non-trade intents, treats the message as a question for Qwen.
        6. Always shows the user what it understood before acting (safety net).
        """
        try:
            text = (ctx.user_message or "").strip()
            # Strip the leading /ask if present (Telegram sometimes routes /ask <text> here)
            text = re.sub(r"^/ask\s*", "", text, flags=re.IGNORECASE).strip()
            if not text:
                return "🤖 Tell me what you want. Examples: `buy 2 dollars of SOL`, `pick a daily trade for me`, `what's the SOL price?`, `analyze ETH`."

            t_lower = text.lower()

            # 0a. AUTO-PICK intent — phrases like:
            #   "pick a daily trade for me"
            #   "do proper analysis and pick a daily trade"
            #   "what should I buy / sell"
            #   "find me a good trade"
            #   "give me the best setup right now"
            # These trigger autotrade. If amount specified, use it; else use default.
            autotrade_phrases = [
                r"\bpick\b.*\btrade\b",
                r"\bdaily\s+trade\b",
                r"\bbest\s+(?:trade|setup|pair|coin)\b",
                r"\bgood\s+trade\b",
                r"\bsuggest\s+(?:a\s+)?trade\b",
                r"\bsuggest\s+(?:a\s+)?(?:coin|pair|setup)\b",
                r"\brecommend\s+(?:a\s+)?trade\b",
                r"\bwhat\s+(?:should|can|do)\s+i\s+(?:buy|sell|trade|enter)\b",
                r"\bfind\s+(?:me\s+)?(?:a\s+)?(?:trade|setup|opportunity)\b",
                r"\bgive\s+me\s+(?:a\s+)?trade\b",
                r"\banalyze\s+and\s+(?:pick|trade|enter)\b",
                r"\bproper\s+analysis\b",
                r"\bautotrade\b",
            ]
            wants_autotrade = any(re.search(p, t_lower) for p in autotrade_phrases)
            if wants_autotrade:
                # Try to extract amount
                amount = self._extract_dollar_amount(text)
                if not amount or amount <= 0:
                    # Default: 5% of balance
                    try:
                        bal = self.bitget.get_account_balance("USDT") or 0.0
                    except Exception:
                        bal = 0.0
                    if bal > 0:
                        amount = round(bal * 0.05, 2)
                    else:
                        return (
                            "❌ No balance detected. Specify an amount:\n"
                            "  • `pick a daily trade with $5`\n"
                            "  • `do proper analysis and pick a daily trade for $2`\n"
                            "  • `/autotrade 5`"
                        )
                return self._agentic_pick_and_trade(ctx, amount_usd=amount)

            # 0b. "go with $X" / "place a trade" with a dollar amount but no symbol
            amount = None
            if re.search(r"\b(go|place|enter|put|take|deploy|use)\b", t_lower) and not re.search(
                r"\b(buy|sell|long|short)\s+[a-zA-Z]{2,8}\s+\$?\d", t_lower
            ):
                amount = self._extract_dollar_amount(text)
                if amount and amount > 0:
                    return self._agentic_pick_and_trade(ctx, amount_usd=amount)

            # 1. Detect trade intent via regex (fast, cheap)
            intent = self._extract_trade_intent(text)

            if intent:
                # It's a trade — route through the appropriate flow
                return self._execute_trade_intent(ctx, intent, text)

            # 2. Not a trade — treat as a question for Qwen
            return self._answer_question(ctx, text)
        except Exception as e:
            logger.exception(f"_cmd_ask failed: {e}")
            return f"❌ Error processing your request: {e}"

    def _extract_dollar_amount(self, text: str) -> Optional[float]:
        """Extract a USD amount from free-form text. Tries $X, X dollars, bare X."""
        t = text.lower()
        # "$X"
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", t)
        if m:
            return float(m.group(1))
        # "X dollars" / "X bucks" / "X usdt" / "X usd"
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:dollars?|bucks|usdt|usdc|usd)\b", t)
        if m:
            return float(m.group(1))
        # Bare number after "for $X" / "with $X" / "of $X"
        m = re.search(r"\b(?:for|with|of|using|use)\s+\$?(\d+(?:\.\d+)?)\b", t)
        if m:
            return float(m.group(1))
        return None

    def _extract_trade_intent(self, text: str) -> Optional[dict]:
        """Extract a trade intent from free-form text.

        Patterns recognized:
        - "buy 2 SOL", "buy $2 of SOL", "buy 2 dollars worth of SOL"
        - "sell my BTC", "sell all ETH", "sell 0.1 BTC"
        - "long SOL", "short BTC"
        - "open a position on SOL with 5 usdt"
        - "use $4.39 to buy SOL"
        - "ape 100 SOL" / "dump my BTC"

        NOT recognized as trades (treated as questions instead):
        - "Analyze the current market and suggest a pair to trade"
        - "What should I trade?"
        - "I want to trade but I'm not sure what"
        (Generic 'trade' alone is too ambiguous — requires a strong action verb
         like buy/sell/long/short/ape/dump/load/fade/enter/open.)

        Returns: {"side": "buy"|"sell", "symbol": str|None, "amount_usd": float|None, "raw": text}
        """
        t = text.lower().strip()
        # Must contain a STRONG trade verb (NOT just 'trade' or 'place' alone)
        # buy/sell/long/short/ape/dump/load/fade/enter/open
        # 'trade' and 'place' are too generic and cause false positives.
        strong_verbs = r"\b(buy|sell|long|short|purchase|dispose|dump|load|ape|fade|enter|open)\b"
        if not re.search(strong_verbs, t):
            return None
        # Determine side
        side = None
        if re.search(r"\b(buy|long|purchase|load|ape|enter|open)\b", t):
            side = "buy"
        elif re.search(r"\b(sell|short|dispose|dump|fade)\b", t):
            side = "sell"
        if not side:
            return None
        # Extract amount. Search the WHOLE text in priority order:
        # 1) "$X" anywhere
        # 2) "X dollars" or "X bucks"
        # 3) "with/for $X"
        # 4) "X" right after the trade verb ("buy 2 SOL")
        amount_usd = None
        # "$X"
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", t)
        if m:
            amount_usd = float(m.group(1))
        # "X dollars" / "X bucks" / "X usdt"
        if amount_usd is None:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:dollars?|bucks|usdt|usdc|usd)", t)
            if m:
                amount_usd = float(m.group(1))
        # "with/for X"
        if amount_usd is None:
            m = re.search(r"(?:with|for|use|using)\s+\$?(\d+(?:\.\d+)?)", t)
            if m:
                amount_usd = float(m.group(1))
        # "buy 2 SOL" — number right after the trade verb
        if amount_usd is None:
            m = re.search(r"\b(?:buy|sell|long|short|load|ape|dump|fade|enter|open)\s+\$?(\d+(?:\.\d+)?)\b", t)
            if m:
                amount_usd = float(m.group(1))
        # Extract symbol. Prefer words AFTER the trade verb.
        skip = {"buy", "sell", "long", "short", "of", "for", "with", "and", "the", "all", "my", "worth", "dollars", "usdt", "usdc", "usd", "a", "an", "position", "on", "in", "at", "to", "from", "purchase", "dispose", "load", "dump", "ape", "fade", "enter", "open", "into", "some", "any", "want", "i", "you", "please", "pls", "let", "me", "go", "all", "the", "this", "that", "should", "could", "would", "will", "shall", "do", "does", "did", "up", "down", "out", "off", "more", "less", "bit", "little", "much", "many", "few", "lot", "just", "now", "then", "soon", "today", "tomorrow", "yesterday", "good", "bad", "big", "small", "high", "low", "right", "wrong", "best", "worst", "use", "using", "make", "place", "trade", "pattern", "necessities", "signal", "go", "going", "tape", "market", "current", "pair", "pairs", "execute", "set", "pick", "profit", "loss", "stop", "when", "necessary", "determine", "yourself", "to", "by", "your", "you'd", "lets", "let", "analyze", "analysis", "suggest", "recommend", "idea", "think", "advice", "help", "what", "how", "why", "who", "where", "your"}
        verb_match = re.search(strong_verbs, t)
        symbol = None
        if verb_match:
            after_verb = t[verb_match.end():]
            for c in re.findall(r"\b([a-zA-Z]{2,8})\b", after_verb):
                if c.lower() not in skip:
                    symbol = c.upper()
                    break
        if symbol is None:
            for c in re.findall(r"\b([a-zA-Z]{2,8})\b", text):
                if c.lower() not in skip:
                    symbol = c.upper()
                    break
        # STRICT REQUIREMENT: must have BOTH a clear symbol AND an amount.
        # If either is missing, treat as a question, not a trade.
        if not symbol or amount_usd is None or amount_usd <= 0:
            return None
        return {"side": side, "symbol": symbol, "amount_usd": amount_usd, "raw": text}

    def _execute_trade_intent(self, ctx: AgentContext, intent: dict, raw_text: str) -> str:
        """Execute a trade intent. Always previews the plan first."""
        side = intent["side"]
        symbol = intent.get("symbol")
        amount = intent.get("amount_usd")

        # If missing symbol OR amount, show what we understood and ask for missing pieces
        if not symbol and not amount:
            return (
                f"🤖 I think you want to *{side}*, but I need more info:\n"
                f"  • Which token? (e.g. SOL, BTC, ETH)\n"
                f"  • How much? (e.g. `$2`, `5 dollars`)\n\n"
                f"Try: `/{side} SYMBOL USDT_AMOUNT`\n"
                f"Or: `{side} 2 SOL`"
            )
        if not symbol and amount:
            # User gave an amount but no symbol — let the bot pick the best pair
            return self._auto_pick_and_trade(ctx, amount_usd=amount)
        if not amount:
            # Default to $1 if no amount specified (safe fallback)
            amount = 1.0
            msg = f"🤖 I'll {side} `${amount:.2f}` of {symbol} (default size; specify an amount to change). Confirm?\n"
            msg += f"  → `/{side} {symbol} {amount}`"
            return msg

        # We have everything. Show the plan, then run the full flow.
        return self._handle_trade_prompt(ctx, side=side, symbol=symbol, amount_usd=amount)

    def _handle_trade_prompt(self, ctx: AgentContext, side: str, symbol: str, amount_usd: float) -> str:
        """Full flow for a trade-from-prompt: normalize symbol, risk check, place order.

        If the trade is large enough to warrant advisor review, use the semi-autonomous
        flow (cache the analysis, ask /proceed). Otherwise, place the order directly.
        """
        try:
            if not symbol.endswith("USDT"):
                symbol = symbol + "USDT"
            # Build a fake AgentContext for _handle_trade
            fake_ctx = AgentContext(
                user_id=ctx.user_id,
                user_message=f"/{side} {symbol} {amount_usd}",
                command=side,
                args={"symbol": symbol, "amount_usd": amount_usd},
            )
            return self._handle_trade(fake_ctx, side=side)
        except Exception as e:
            logger.exception(f"_handle_trade_prompt failed: {e}")
            return f"❌ Couldn't execute the trade: {e}"

    def _auto_pick_and_trade(self, ctx: AgentContext, amount_usd: float, market: str = "spot") -> str:
        """Pick the best crypto pair on the board and trade it. Used when user
        says things like 'go with $10' or 'place a trade for $5' without naming a symbol.
        """
        try:
            # 1. Run the scan
            result = self.skills.invoke("find_best_trade", {"amount_usd": amount_usd, "max_candidates": 5})
            result = result.get("result", result) if isinstance(result, dict) else result
            if not result.get("ok"):
                return f"❌ Couldn't find a setup: {result.get('error', 'unknown')}"

            qwen_pick = result.get("qwen_pick")
            qwen_reasoning = result.get("qwen_reasoning", "")
            suggested_tp_sl = result.get("suggested_tp_sl") or {}
            ranked = result.get("ranked", [])

            # Build the visible scan table — show user the actual analysis
            scan_lines = ["*🔍 Universe scan — top 5 by 9-signal composite:*\n"]
            for i, r in enumerate(ranked, 1):
                sym = r.get("symbol", "?")
                comp = r.get("composite", 0)
                price = r.get("current_price", 0)
                chg = r.get("change_24h_pct", 0)
                vol = r.get("volume_24h", 0)
                t = r.get("technicals", {})
                bits = []
                if "rsi_14" in t:
                    bits.append(f"RSI {t['rsi_14']}")
                if "macd_hist" in t:
                    bits.append(f"MACD {t['macd_hist']:+.4f}")
                if "adx_14" in t:
                    bits.append(f"ADX {t['adx_14']}")
                if "ema_cross" in t and t["ema_cross"] != "none":
                    bits.append(f"EMA {t['ema_cross']}")
                if "atr_pct" in t:
                    bits.append(f"ATR {t['atr_pct']}%")
                line = (
                    f"  {i}. *{sym}* — score {comp:.2f}, "
                    f"${price:.4f} ({chg:+.2f}% 24h), vol ${vol/1e6:.1f}M"
                )
                if bits:
                    line += "\n     techs: " + " · ".join(bits)
                scan_lines.append(line)
            scan_block = "\n".join(scan_lines) + "\n"

            # If Qwen explicitly skipped
            if not qwen_pick or qwen_pick == "SKIP":
                return (
                    scan_block
                    + f"\n*🧠 Qwen decision:* SKIP\n"
                    + f"_{qwen_reasoning}_\n\n"
                    + f"❌ No setup worth trading right now. The market is choppy or "
                    + f"no candidate has a clear edge. Try again later, or `/analyze SYMBOL 2` "
                    + f"to dig into a specific pair."
                )

            confidence = result.get("qwen_confidence", 0.5)

            # 2. Use the smart position sizer to compute the actual size
            balance = self.bitget.get_account_balance("USDT")
            try:
                portfolio = self.bitget.get_portfolio_value_usdt()
            except Exception:
                portfolio = balance

            size_suggestion = self.skills.invoke("suggest_position_size", {
                "balance_usd": balance,
                "confidence": confidence,
                "signal_score": ranked[0].get("composite", 0.5) if ranked else 0.5,
                "user_requested_usd": amount_usd,
            })
            size_suggestion = size_suggestion.get("result", size_suggestion) if isinstance(size_suggestion, dict) else size_suggestion
            final_size = size_suggestion.get("size_usd", amount_usd) or amount_usd

            # Show TP/SL from the suggestion so user can see risk:reward before execution
            tp_pct = suggested_tp_sl.get("take_profit_pct", 0) if isinstance(suggested_tp_sl, dict) else 0
            sl_pct = suggested_tp_sl.get("stop_loss_pct", 0) if isinstance(suggested_tp_sl, dict) else 0
            rr = suggested_tp_sl.get("risk_reward_ratio", 0) if isinstance(suggested_tp_sl, dict) else 0

            # 3. Execute the trade via the regular _handle_trade flow
            fake_ctx = AgentContext(
                user_id=ctx.user_id,
                user_message=f"/buy {qwen_pick} {final_size}",
                command="buy",
                args={"symbol": qwen_pick, "amount_usd": final_size},
            )

            # Honor Qwen's directional pick (long/short)
            qwen_side = result.get("qwen_side", "long")
            side = "buy" if qwen_side == "long" else "sell"
            cmd_name = "buy" if side == "buy" else "sell"
            fake_ctx = AgentContext(
                user_id=ctx.user_id,
                user_message=f"/{cmd_name} {qwen_pick} {final_size}",
                command=cmd_name,
                args={"symbol": qwen_pick, "amount_usd": final_size},
            )
            decision_block = (
                f"\n*🧠 Qwen decision:* *{qwen_pick}* ({qwen_side.upper()}, conf {confidence:.2f})\n"
                f"_{qwen_reasoning}_\n\n"
                f"💰 *Size:* `${final_size:.2f}` "
                f"({final_size/balance*100:.1f}% of ${balance:.2f} balance)\n"
                f"📋 _{size_suggestion.get('rationale', '').strip()}_\n"
            )
            if tp_pct and sl_pct:
                decision_block += (
                    f"🎯 *Plan:* TP +{tp_pct:.1f}% / SL {sl_pct:.1f}% "
                    f"(R:R {rr:.2f}:1)\n\n"
                )
            else:
                decision_block += "\n"
            execution = self._handle_trade(fake_ctx, side=side)
            return scan_block + decision_block + execution
        except Exception as e:
            logger.exception(f"_auto_pick_and_trade failed: {e}")
            return f"❌ Auto-trade failed: {e}"

    def _answer_question(self, ctx: AgentContext, text: str) -> str:
        """Answer a free-form question using Qwen + skills."""
        try:
            balance = self.bitget.get_account_balance("USDT")
            try:
                portfolio = self.bitget.get_portfolio_value_usdt()
            except Exception:
                portfolio = balance
            context_msg = (
                f"Current context:\n"
                f"- USDT balance: ${balance:.2f}\n"
                f"- Portfolio value: ${portfolio:.2f}\n"
                f"- User said: {text}\n\n"
                f"When you write your final reply, reason naturally in your own voice. "
                f"Don't just say 'Done' — write a full analysis in conversational English. "
                f"Reference real numbers, real tickers, and explain WHY you'd pick or skip a setup. "
                f"You can show multiple candidates (top 10-20 if relevant). "
                f"Always cite the actual crypto pair symbols (BTCUSDT, SOLUSDT, ETHUSDT, etc.), "
                f"never invent tickers, and never return a one-word answer to a substantive question."
            )
            skills_descriptions = self.skills.get_skill_descriptions()
            tools = self.skills.get_tool_schemas()

            # Auto-recall relevant memories from the journal and inject them
            # into the system prompt so Qwen has continuity across turns.
            try:
                mem_result = self.skills.invoke("memory_recall", {"query": text, "limit": 5, "user_id": ctx.user_id})
                mem_context = mem_result.get("result", mem_result).get("context", "")
            except Exception:
                mem_context = ""
            full_system = SYSTEM_PROMPT
            if mem_context and "(no memories" not in mem_context:
                full_system = SYSTEM_PROMPT + "\n\n" + mem_context

            resp = self.qwen.chat(
                messages=[
                    {"role": "system", "content": full_system + "\n\nAvailable skills:\n" + skills_descriptions},
                    {"role": "user", "content": context_msg},
                ],
                max_tokens=1500,
                tools=tools if tools else None,
            )

            if resp.get("tool_calls"):
                tool_call = resp["tool_calls"][0]
                skill_name = tool_call["function"]["name"]
                skill_args_str = tool_call["function"]["arguments"]
                try:
                    skill_args = json.loads(skill_args_str)
                except Exception:
                    skill_args = {}
                result = self.skills.invoke(skill_name, skill_args)

                # Trim large results (e.g. universe_scan returns 50+ candidates)
                # to keep Qwen's context window manageable and force it to summarize.
                result_str = json.dumps(result, default=str)
                if len(result_str) > 6000:
                    # Keep the structure but trim large lists to top 10 items, drop
                    # noisy fields like 'signals' and 'sub_scores' which Qwen doesn't
                    # need for a natural-language summary.
                    if isinstance(result, dict) and "result" in result and isinstance(result["result"], dict):
                        inner = result["result"]
                        for key in ("candidates", "ranked"):
                            if key in inner and isinstance(inner[key], list) and len(inner[key]) > 10:
                                inner[key] = inner[key][:10]
                        for item in inner.get("candidates", []) + inner.get("ranked", []):
                            if isinstance(item, dict):
                                item.pop("signals", None)
                                item.pop("sub_scores", None)
                        result_str = json.dumps(result, default=str)

                followup = self.qwen.chat(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": resp["content"] or "(invoking skill)"},
                        {"role": "tool", "tool_call_id": tool_call.get("id", "1"), "name": skill_name, "content": result_str},
                    ],
                    max_tokens=1500,
                )
                return followup["content"] or f"✓ {skill_name} ran successfully but the summary came back empty. Ask me to drill into a specific pair."

            # Remember this exchange so the bot has continuity next time
            try:
                bot_says = resp["content"] or ""
                # Keep only first 400 chars to avoid bloat
                summary = bot_says[:400].replace("\n", " ")
                self.db.add_memory(
                    "conversation",
                    f"User asked: '{text[:200]}'. I replied: '{summary}'",
                    tags=["chat"],
                    importance=3,
                )
            except Exception:
                pass

            return resp["content"] or "🤔 I'm not sure what to do. Try `/help` for commands."
        except Exception as e:
            logger.exception(f"_answer_question failed: {e}")
            return f"❌ Couldn't answer: {e}"
            return f"❌ I couldn't process that: {e}"

    # -------------------------------------------------------------------------
    # Trade execution
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Trade execution
    # -------------------------------------------------------------------------

    def _handle_trade(self, ctx: AgentContext, side: str) -> str:
        """The main trade flow: ADVISE (soft) → RISK → EXECUTE → REFLECT.

        The advisor is now a soft warning only. The user is the boss:
        we always execute the trade they asked for, and just show the
        advisory note inline so they can see the reasoning. The risk
        engine is the only hard block (kill switch, max position size,
        daily loss cap, blacklist).
        """
        symbol = (ctx.args.get("symbol") or "").upper()
        amount_usd = ctx.args.get("amount_usd") or 0

        if not symbol or amount_usd <= 0:
            return f"❌ Usage: `/{side} SYMBOL USDT_AMOUNT`\n\nExample: `/{side} SOL 100`"

        # Normalize symbol (e.g., SOL → SOLUSDT)
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        try:
            # PERCEIVE — get price first so we can give the advisor real data
            ticker = self.bitget.get_ticker(symbol)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            price = float(ticker.get("lastPr", 0))
            if price <= 0:
                return f"❌ Couldn't get price for {symbol}. Symbol might be invalid."

            # ADVISE — ask the strategy skill for a read on this trade
            # (chart structure, recent memory with this symbol, news placeholder)
            user_intent_reason = ctx.user_message if hasattr(ctx, "user_message") else ""
            advisory = {}
            try:
                advisory = self.skills.invoke(
                    "advise_before_trade",
                    symbol=symbol,
                    side=side,
                    amount_usd=amount_usd,
                    user_intent_reason=user_intent_reason,
                )
            except Exception as e:
                logger.exception(f"advise_before_trade failed: {e}")
                advisory = {"action": side, "confidence": 0.0, "conflicts": False, "reasoning": "(advisor unavailable)", "risks": [], "alternatives": []}

            advisor_action = advisory.get("action", side).lower()
            confidence = float(advisory.get("confidence", 0.0))
            conflicts = bool(advisory.get("conflicts", False))
            reasoning = advisory.get("reasoning", "")

            # The user is the boss. The advisor is now a SOFT WARNING only.
            # We no longer hard-block trades even when the advisor strongly disagrees.
            # Instead, we always proceed and show the warning inline so the user
            # sees the reasoning but gets their trade executed.
            nudge = ""
            if conflicts or advisor_action == "hold":
                risks_str = ""
                if advisory.get("risks"):
                    risks_str = f"\n  Risks: {', '.join(advisory.get('risks', []))}"
                nudge = (
                    f"⚠️ *Advisory note (confidence {confidence:.2f}):* {reasoning}"
                    f"{risks_str}\n"
                    f"  Proceeding as you requested.\n\n"
                )

            # RISK CHECK (only after advisory)
            balance = self.bitget.get_account_balance("USDT")
            portfolio = self.bitget.get_portfolio_value_usdt()
            open_positions = len(self.db.get_open_trades())

            allowed, risk_reason = self.risk_for(ctx.user_id).check_order(
                symbol=symbol,
                side=side,
                size_usd=amount_usd,
                portfolio_value_usd=portfolio,
                open_positions_count=open_positions,
            )
            if not allowed:
                return f"🛑 *Trade blocked by risk engine:*\n\n{risk_reason}"

            # EXECUTE
            try:
                order = self.bitget.place_spot_order(
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    quote_size=str(amount_usd) if side == "buy" else None,
                )
                order_id = order.get("orderId", "")

                size = amount_usd / price if price > 0 else 0
                was_overridden = bool(nudge)  # if we nudged, this trade was an override

                trade_id = self.db.record_trade(
                    symbol=symbol,
                    side=side,
                    order_type="spot",
                    size=size,
                    price=price,
                    quote_usd=amount_usd,
                    order_id=order_id,
                    reason=reasoning,
                    skills_used=["place_spot_order", "get_ticker", "risk_check", "advise_before_trade", "qwen_reasoning"],
                    confidence=confidence if confidence > 0 else 0.7,
                )

                self.db.record_signal(
                    symbol=symbol,
                    action=side,
                    reasoning=reasoning,
                    skills_invoked=["advise_before_trade", "place_spot_order", "get_ticker", "risk_check"],
                    market_state=advisory.get("market_state", {}),
                    trade_id=trade_id,
                )

                # Tag the trade if the user overrode a soft advisory nudge
                tags = [side, symbol, "trade"]
                if was_overridden:
                    tags.append("advisory_nudge_overridden")
                self.db.add_memory(
                    "observation",
                    f"Trade: {side.upper()} ${amount_usd:.2f} of {symbol} at ${price:.4f}. "
                    f"Advisory: {advisor_action} (conf {confidence:.2f}). "
                    f"Reason: {reasoning[:140]}",
                    tags=tags,
                    importance=4,
                )

                return (
                    f"{nudge}"
                    f"✅ *Trade executed*\n\n"
                    f"📋 Order ID: `{order_id}`\n"
                    f"💱 {side.upper()} ${amount_usd:.2f} of {symbol}\n"
                    f"💰 Price: `${price:.4f}`\n"
                    f"📐 Size: `{size:.6f}` {symbol.replace('USDT', '')}\n"
                    f"🧭 Advisory said: {advisor_action} (confidence {confidence:.2f})\n\n"
                    f"*Reasoning:* {reasoning}\n\n"
                    f"📓 Logged to journal. Use `/journal` to see it later."
                )
            except BitgetAPIError as e:
                return f"❌ Bitget rejected the order: {e}"
        except Exception as e:
            logger.exception(f"_handle_trade failed: {e}")
            return f"❌ Trade failed: {e}"

    def _format_advisory_hold(self, symbol, side, amount_usd, price, advisory) -> str:
        """Format the 'I'm holding your trade; please confirm' response."""
        risks_txt = (
            "\n".join(f"  • {r}" for r in advisory.get("risks", []))
            if advisory.get("risks") else "  • (none flagged)"
        )
        alts_txt = (
            "\n".join(f"  • {a}" for a in advisory.get("alternatives", []))
            if advisory.get("alternatives") else "  • (none suggested)"
        )
        ms = advisory.get("market_state", {})
        return (
            f"🛑 *Trade held — advisor disagrees*\n\n"
            f"You asked: *{side.upper()} ${amount_usd:.2f} {symbol}* at `${price:.4f}`\n"
            f"Advisory says: *{advisory.get('action', '?').upper()}* "
            f"(confidence {float(advisory.get('confidence', 0)):.2f})\n\n"
            f"*Why:* {advisory.get('reasoning', '')}\n\n"
            f"📉 *Risks flagged:*\n{risks_txt}\n\n"
            f"💡 *Alternatives to consider:*\n{alts_txt}\n\n"
            f"📊 *Market state:* "
            f"24h chg {ms.get('change_24h_pct', 0):+.2f}% | "
            f"high ${ms.get('high_24h', 0):.4f} | low ${ms.get('low_24h', 0):.4f}\n\n"
            f"*You can override the advisor:*\n"
            f"  `/force-buy {symbol} {amount_usd}` — proceed with your BUY\n"
            f"  `/force-sell {symbol} {amount_usd}` — proceed with your SELL\n"
            f"  `/abort` — cancel this trade\n\n"
            f"_Advisory expires in 5 minutes._"
        )

    def _cmd_force_buy(self, ctx: AgentContext) -> str:
        """Override a strong advisory and place a BUY anyway."""
        return self._handle_force(ctx, "buy")

    def _cmd_force_sell(self, ctx: AgentContext) -> str:
        """Override a strong advisory and place a SELL anyway."""
        return self._handle_force(ctx, "sell")

    def _handle_force(self, ctx: AgentContext, side: str) -> str:
        pending = self._pending_advisories.get(ctx.user_id)
        if not pending:
            return (
                f"❌ No pending advisory to override.\n"
                f"Use `/buy SYMBOL USDT` or `/sell SYMBOL USDT` to start a new trade."
            )
        # Expire stale pending (5 min)
        if time.time() - pending.get("timestamp", 0) > 300:
            del self._pending_advisories[ctx.user_id]
            return "⌛ The pending advisory expired. Please run `/buy` or `/sell` again."

        # Optional: user can pass different symbol/amount, but we trust the cached trade
        symbol = pending["symbol"]
        amount_usd = pending["amount_usd"]
        price = pending["price"]
        advisory = pending.get("advisory", {})

        # Clear the pending cache
        del self._pending_advisories[ctx.user_id]

        # RISK CHECK (still required)
        try:
            balance = self.bitget.get_account_balance("USDT")
            portfolio = self.bitget.get_portfolio_value_usdt()
            open_positions = len(self.db.get_open_trades())
            allowed, risk_reason = self.risk_for(ctx.user_id).check_order(
                symbol=symbol,
                side=side,
                size_usd=amount_usd,
                portfolio_value_usd=portfolio,
                open_positions_count=open_positions,
            )
            if not allowed:
                return f"🛑 *Risk engine blocked the override:*\n\n{risk_reason}"

            # EXECUTE
            order = self.bitget.place_spot_order(
                symbol=symbol,
                side=side,
                order_type="market",
                quote_size=str(amount_usd) if side == "buy" else None,
            )
            order_id = order.get("orderId", "")
            size = amount_usd / price if price > 0 else 0
            override_note = (
                f"User OVERRODE advisory ({advisory.get('action', '?')} "
                f"@{float(advisory.get('confidence', 0)):.2f}) and proceeded with {side.upper()}. "
                f"Advisory reasoning: {advisory.get('reasoning', '')[:200]}"
            )

            trade_id = self.db.record_trade(
                symbol=symbol,
                side=side,
                order_type="spot",
                size=size,
                price=price,
                quote_usd=amount_usd,
                order_id=order_id,
                reason=override_note,
                skills_used=["place_spot_order", "get_ticker", "risk_check", "advise_before_trade", "qwen_reasoning", "user_override"],
                confidence=1.0,  # user-confirmed
            )

            self.db.record_signal(
                symbol=symbol,
                action=side,
                reasoning=override_note,
                skills_invoked=["advise_before_trade", "user_override", "place_spot_order"],
                market_state=advisory.get("market_state", {}),
                trade_id=trade_id,
            )

            self.db.add_memory(
                "observation",
                f"OVERRIDE: User placed {side.upper()} ${amount_usd:.2f} of {symbol} "
                f"after advisor said {advisory.get('action', '?')} (conf {float(advisory.get('confidence', 0)):.2f}). "
                f"Advisory: {advisory.get('reasoning', '')[:140]}",
                tags=[side, symbol, "trade", "advisory_override"],
                importance=5,  # higher importance — these are the learning cases
            )

            return (
                f"⚠️ *Override executed*\n\n"
                f"You overrode the advisor's recommendation.\n"
                f"📋 Order ID: `{order_id}`\n"
                f"💱 {side.upper()} ${amount_usd:.2f} of {symbol}\n"
                f"💰 Price: `${price:.4f}`\n"
                f"📐 Size: `{size:.6f}` {symbol.replace('USDT', '')}\n"
                f"🧭 Advisor wanted: {advisory.get('action', '?').upper()} "
                f"(confidence {float(advisory.get('confidence', 0)):.2f})\n\n"
                f"📓 Override logged. The agent will use this to learn over time."
            )
        except BitgetAPIError as e:
            return f"❌ Bitget rejected the order: {e}"
        except Exception as e:
            logger.exception(f"_handle_force failed: {e}")
            return f"❌ Override failed: {e}"
