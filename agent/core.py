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
from typing import Any, Optional
from dataclasses import dataclass

from clients.bitget import BitgetClient, BitgetAPIError
from clients.qwen import QwenClient
from db.database import Database
from risk.engine import RiskEngine

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
- You have 100+ skills (functions) you can call. See the tools list.
- You can read market data, place orders, manage risk, analyze onchain signals.
- You can score counterparties for sybil risk before entering low-cap positions.
- You can check MEV exposure before swaps.
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

    Yoruba solfège: re (do'), mi (re), do (mi), etc. We use the standard Yoruba greetings:
    - Ọniṣọwọ́ káàlẹ́   — generic "good day" (safe default)
    - Ọniṣọwọ́ ẹ káàrọ̀  — "good morning" (before noon)
    - Ọniṣọwọ́ ẹ káàsán  — "good afternoon" (noon–4pm)
    - Ọniṣọwọ́ ẹ káàlẹ́  — "good evening" (4pm–7pm, also generic fallback)
    - Ọniṣọwọ́ ẹ káàlẹ́ òru — "good night" (7pm–5am)

    All times computed in WAT regardless of server timezone, so the bot greets
    its target audience (West African users) on their local clock.
    """
    from datetime import datetime, timezone, timedelta
    wat = timezone(timedelta(hours=1))  # WAT = UTC+1 (no DST in Nigeria)
    now_wat = datetime.now(wat)
    hour = now_wat.hour

    if 5 <= hour < 12:
        return "Ọniṣọwọ́ ẹ káàrọ̀ ☀️"  # good morning
    elif 12 <= hour < 16:
        return "Ọniṣọwọ́ ẹ káàsán 🌤️"  # good afternoon
    elif 16 <= hour < 19:
        return "Ọniṣọwọ́ ẹ káàlẹ́ 🌇"  # good evening
    else:
        return "Ọniṣọwọ́ ẹ káàlẹ́ òru 🌙"  # good night


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
            "I have 100+ skills, MEV awareness, sybil scoring, "
            "and a memory that learns from every trade.\n\n"
            "*Quick start:*\n"
            "• `/intro` — read Àkànjí's full origin story\n"
            "• `/status` — your portfolio + P&L\n"
            "• `/buy SOL 2` — buy $2 of SOL (Qwen advises first, you can override)\n"
            "• `/analyze ETH 2` — deep analysis + bot's TP/SL\n"
            "• `/autotrade 2` — I scan the market, pick the best, execute\n"
            "• `/strategist start` — autonomous mode on a loop\n"
            "• `/skills` — list my 100+ skills\n"
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
            "*Oniṣòwò commands:*\n\n"
            "*Trading:*\n"
            "• `/buy SYMBOL USDT_AMOUNT` — e.g., `/buy SOL 100`\n"
            "• `/sell SYMBOL USDT_AMOUNT` — e.g., `/sell BTC 50`\n"
            "• `/cancel ORDER_ID` — cancel a pending order\n\n"
            "*Portfolio & analysis:*\n"
            "• `/status` — portfolio + balance + open positions\n"
            "• `/balance` — current cash balance\n"
            "• `/pnl` — total P&L from journal\n"
            "• `/journal` — recent trade journal with reasoning\n"
            "• `/review` — last 7 days, with lessons learned\n\n"
            "*Intelligence:*\n"
            "• `/price SYMBOL` — current price + 24h stats\n"
            "• `/skills` — list all 100+ skills\n"
            "• `/skill NAME` — invoke a specific skill\n"
            "• `/mev TOKEN` — check MEV exposure\n"
            "• `/sybil 0x...` — score a wallet\n\n"
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
        is_qwen = "qwen" in model.lower()
        brain_line = (
            f"*Powered by:* {display} (Alibaba Cloud, via Bitget hackathon proxy)"
            if is_qwen
            else f"*LLM brain:* {display}"
        )
        return (
            "*Oniṣòwò* (oh-nee-SHAW-woh) — Yoruba for *merchant*.\n\n"
            "Built by [@ruzkypazzy](https://github.com/ruzkypazzy) for the "
            "[Bitget AI Base Camp Hackathon S1](https://bitget-ai.gitbook.io/base-camp-hackathon-s1-en).\n\n"
            f"{brain_line}\n"
            f"*Endpoint:* `{base_url}`\n\n"
            "*Stack:*\n"
            "• *LLM:* Qwen 3.6 Plus (Alibaba Cloud) — every reasoning call goes through Qwen\n"
            "• *Exchange:* Bitget spot + futures (58 API tools)\n"
            "• *Surface:* Telegram (you are here)\n"
            "• *Storage:* SQLite (local file)\n"
            "• *Code:* Python 3.10+, open-source, MIT\n\n"
            "*Differentiation:*\n"
            "• *100+ skills* organized in 9 tiers (vs typical 5-10)\n"
            "• *MEV-aware execution* — checks sandwich-attack risk before every swap\n"
            "• *Sybil counterparty scoring* — refuses rug-prone tokens\n"
            "• *Recursive self-improvement* — reviews past trades, writes new rules to memory\n"
            "• *Qwen-powered* — Qwen 3.6 Plus is the brain; every decision is a Qwen decision\n"
            "• *Self-hostable* — your keys never leave your machine\n\n"
            "Source: [github.com/ruzkypazzy/Onisowo](https://github.com/ruzkypazzy/Onisowo)"
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
                f"📋 Open positions: `{len(positions)}`\n\n"
            )

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
            last = float(ticker.get("lastPr", 0))
            change_24h = float(ticker.get("change24h", 0))
            high_24h = float(ticker.get("high24h", 0))
            low_24h = float(ticker.get("low24h", 0))
            vol_24h = float(ticker.get("baseVolume", 0))

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
        allowed, risk_reason = self.risk.check_order(
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

        try:
            result = self.skills.invoke("find_best_trade", {"amount_usd": amount_usd, "max_candidates": 5})
            result = result.get("result", result) if isinstance(result, dict) else result
            if not result.get("ok"):
                return f"❌ Autonomous scan failed: {result.get('error', 'unknown')}"

            qwen_pick = result.get("qwen_pick")
            qwen_conf = result.get("qwen_confidence", 0)
            suggested = result.get("suggested_tp_sl") or {}
            executes = result.get("executes", False)

            if not executes or not qwen_pick or qwen_pick == "SKIP":
                # Safety net: don't auto-execute. Show picks and let user /analyze + /proceed
                ranked = result.get("ranked", [])
                lines = [
                    f"🤖 *Autonomous scan complete — ${amount_usd:.2f}*\n",
                    f"🧠 *Qwen's verdict:* *{qwen_pick or 'SKIP'}* (confidence {qwen_conf:.2f})",
                    f"   _{result.get('qwen_reasoning', '')}_",
                    "",
                    f"⏸ *Safety net: not auto-executing.* Top picks:",
                ]
                for i, r in enumerate(ranked[:3], 1):
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
                    lines.append(f"{medal} {r['symbol']} (composite {r['composite']:.2f})")
                lines.append("")
                lines.append("→ `/analyze SYMBOL " + f"{amount_usd}" + "` to drill in")
                lines.append("→ `/autotrade confirm` to force-execute the suggested pick (if any)")
                return "\n".join(lines)

            # Auto-execute path
            sym = qwen_pick
            side = "buy"
            tp_sl = suggested
            thesis = (
                f"Autonomous pick: BUY ${amount_usd:.2f} {sym}. "
                f"Qwen confidence: {qwen_conf:.2f}. "
                f"Reason: {result.get('qwen_reasoning', '')[:200]}"
            )

            # Risk check
            portfolio = self.bitget.get_portfolio_value_usdt()
            open_positions = len(self.db.get_open_trades())
            allowed, risk_reason = self.risk.check_order(
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
        s = self.risk.get_status()
        return (
            "*Risk Engine* 🛡️\n\n"
            f"• Max trade: `${s['max_trade_usd']:.2f}`\n"
            f"• Max position: `{s['max_position_pct']*100:.0f}%` of portfolio\n"
            f"• Max drawdown: `{s['max_drawdown_pct']*100:.0f}%`\n"
            f"• Max open trades: `{s['max_open_trades']}`\n"
            f"• Max leverage: `{s['max_leverage']}x`\n"
            f"• Blacklist: `{', '.join(s['blacklist'])}`\n"
            f"• Kill switch: `{'🔴 ACTIVE' if s['kill_switch_active'] else '🟢 OFF'}`"
        )

    def _cmd_kill(self, ctx: AgentContext) -> str:
        reason = " ".join(ctx.args.get("extra", [])) or "Manual"
        self.risk.activate_kill_switch(reason=reason)
        return f"🛑 *Kill switch activated.*\n\nReason: {reason}\n\nNo trades will be placed until you `/release`."

    def _cmd_release(self, ctx: AgentContext) -> str:
        self.risk.release_kill_switch()
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

    def _cmd_settings(self, ctx: AgentContext) -> str:
        # Show current settings
        return self._cmd_risk(ctx) + "\n\n*To update:* (coming soon)"

    def _cmd_journal(self, ctx: AgentContext) -> str:
        trades = self.db.get_recent_trades(limit=10)
        if not trades:
            return "📓 Journal is empty. No trades yet."

        lines = ["*Trade Journal* 📓\n"]
        for t in trades:
            pnl_emoji = "🟢" if t.get("pnl_usd", 0) > 0 else "🔴" if t.get("pnl_usd", 0) < 0 else "⚪"
            lines.append(
                f"{pnl_emoji} `{t['opened_at'][:10]}` "
                f"{t['side'].upper()} {t['symbol']} "
                f"${t['quote_usd']:.2f} — `{t['status']}`"
            )
            if t.get("reason"):
                reason_short = t["reason"][:80] + "..." if len(t["reason"]) > 80 else t["reason"]
                lines.append(f"   _Reason:_ {reason_short}")
        return "\n".join(lines)

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

    def _cmd_ask(self, ctx: AgentContext) -> str:
        """Prompt bot: take any free-form text and act on it.

        This is the user's primary interface. The bot:
        1. Extracts a trade intent (if any): "buy 2 SOL", "sell all my BTC", etc.
        2. For trade intents, runs the full perceive→advise→risk→execute→reflect flow.
        3. For non-trade intents, treats the message as a question for Qwen.
        4. Always shows the user what it understood before acting (safety net).
        """
        try:
            text = (ctx.user_message or "").strip()
            # Strip the leading /ask if present (Telegram sometimes routes /ask <text> here)
            text = re.sub(r"^/ask\s*", "", text, flags=re.IGNORECASE).strip()
            if not text:
                return "🤖 Tell me what you want. Examples: `buy 2 dollars of SOL`, `sell my BTC`, `what's the SOL price?`, `analyze ETH`."

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

    def _extract_trade_intent(self, text: str) -> Optional[dict]:
        """Extract a trade intent from free-form text.

        Patterns recognized:
        - "buy 2 SOL", "buy $2 of SOL", "buy 2 dollars worth of SOL"
        - "sell my BTC", "sell all ETH", "sell 0.1 BTC"
        - "long SOL", "short BTC"
        - "trade ETH with 2 dollars"
        - "open a position on SOL with 5 usdt"
        Returns: {"side": "buy"|"sell", "symbol": str|None, "amount_usd": float|None, "raw": text}
        """
        t = text.lower()
        # Must contain a buy/sell/long/short verb
        if not re.search(r"\b(buy|sell|long|short|purchase|dispose|dump|load|ape|fade|enter|open)\b", t):
            return None
        # Determine side
        side = None
        if re.search(r"\b(buy|long|purchase|load|ape|enter|open)\b", t):
            side = "buy"
        elif re.search(r"\b(sell|short|dispose|dump|fade)\b", t):
            side = "sell"
        if not side:
            return None
        # Extract amount
        amount_usd = None
        # "buy 2 SOL", "buy 2 dollars of SOL"
        m = re.search(r"(?:buy|sell|long|short|load|ape|dump|fade|enter|open)\s+\$?(\d+(?:\.\d+)?)\s*(?:dollars?|usdt|usdc|usd|of)?", t)
        if m:
            amount_usd = float(m.group(1))
        # "buy $2 of SOL" (dollar sign first)
        if amount_usd is None:
            m = re.search(r"\$\s*(\d+(?:\.\d+)?)", t)
            if m:
                amount_usd = float(m.group(1))
        # "with 2 dollars", "with $5"
        if amount_usd is None:
            m = re.search(r"with\s+\$?(\d+(?:\.\d+)?)\s*(?:dollars?|usdt|usdc|usd)?", t)
            if m:
                amount_usd = float(m.group(1))
        # "for 2", "for $2"
        if amount_usd is None:
            m = re.search(r"for\s+\$?(\d+(?:\.\d+)?)", t)
            if m:
                amount_usd = float(m.group(1))
        # Extract symbol. Prefer words AFTER the trade verb, so "I want to ape
        # into SOL" picks SOL (not WANT).
        skip = {"buy", "sell", "long", "short", "of", "for", "with", "and", "the", "all", "my", "worth", "dollars", "usdt", "usdc", "usd", "a", "an", "position", "on", "in", "at", "to", "from", "purchase", "dispose", "load", "dump", "ape", "fade", "enter", "open", "into", "some", "any", "want", "i", "please", "pls", "let", "me", "go", "all", "the", "this", "that", "should", "could", "would", "will", "shall", "do", "does", "did"}
        # Find substring after the trade verb
        verb_match = re.search(r"\b(buy|sell|long|short|purchase|dispose|dump|load|ape|fade|enter|open)\b", t)
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
        if not symbol:
            return {"side": side, "symbol": None, "amount_usd": amount_usd, "raw": text}
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
        if not symbol:
            return (
                f"🤖 Got the {side} for `${amount}`. Which token?\n"
                f"Reply like: `SOL` or `BTCUSDT`"
            )
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
                f"- User said: {text}\n"
            )
            skills_descriptions = self.skills.get_skill_descriptions()
            tools = self.skills.get_tool_schemas()

            resp = self.qwen.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT + "\n\nAvailable skills:\n" + skills_descriptions},
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
                followup = self.qwen.chat(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": resp["content"] or "(invoking skill)"},
                        {"role": "tool", "tool_call_id": tool_call.get("id", "1"), "name": skill_name, "content": json.dumps(result)},
                    ],
                    max_tokens=1000,
                )
                return followup["content"] or "Done."

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
        """The main trade flow: ADVISE → RISK → (maybe HOLD) → EXECUTE → REFLECT.

        New advisory step: before risk check, we ask the strategy skill
        `advise_before_trade` to analyze chart + news + market state and tell
        us what the right move is. If the advisor strongly disagrees with the
        user's intent (confidence >= 0.7 and action != user's side), we hold
        the trade and ask the user to /force-buy, /force-sell, or /abort.
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
                advisory = self.skills.call(
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

            # STRONG CONFLICT: advisor disagrees with high confidence
            # → hold the trade, give the advisory, ask user to confirm
            if conflicts and confidence >= 0.7:
                self._pending_advisories[ctx.user_id] = {
                    "side": side,
                    "symbol": symbol,
                    "amount_usd": amount_usd,
                    "price": price,
                    "advisory": advisory,
                    "timestamp": time.time(),
                }
                return self._format_advisory_hold(symbol, side, amount_usd, price, advisory)

            # SOFT NUDGE: conflicts but low confidence (or advisor said "hold")
            # → warn briefly, then proceed
            nudge = ""
            if conflicts or advisor_action == "hold":
                if advisory.get("risks"):
                    nudge = (
                        f"⚠️ *Advisory note:* {reasoning}\n"
                        f"  Risks: {', '.join(advisory.get('risks', []))}\n"
                        f"  (Confidence {confidence:.2f} — proceeding as you asked.)\n\n"
                    )

            # RISK CHECK (only after advisory)
            balance = self.bitget.get_account_balance("USDT")
            portfolio = self.bitget.get_portfolio_value_usdt()
            open_positions = len(self.db.get_open_trades())

            allowed, risk_reason = self.risk.check_order(
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
            allowed, risk_reason = self.risk.check_order(
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
