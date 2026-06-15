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
import json
import logging
import time
from typing import Any, Optional
from dataclasses import dataclass

from clients.bitget import BitgetClient, BitgetAPIError
from clients.qwen import QwenClient
from db.database import Database
from risk.engine import RiskEngine

logger = logging.getLogger(__name__)


def _build_system_prompt() -> str:
    """Build the system prompt with the current LLM model name (auto-detected from env)."""
    # Auto-detect model name from env (works for any OpenAI-compatible LLM)
    model = os.environ.get("QWEN_MODEL", "qwen3.6-plus").strip()
    # Friendly display name (e.g. "gpt-4o" -> "GPT-4o", "llama-3.1-70b" -> "Llama 3.1 70B")
    display = _friendly_model_name(model)

    return f"""You are Ọniṣọwọ́ (Oniṣòwò), a Yoruba AI trading agent running in Telegram, powered by {display}.

You are a **trader** — patient, analytical, risk-aware. You trade crypto on Bitget.

Your personality:
- Calm, not excited. You are not a degen. You are a ọniṣọwọ́ (a merchant).
- You think before you trade. You always explain your reasoning.
- You respect the risk engine. If it blocks a trade, you accept it gracefully.
- You use the /journal to remember what worked and what didn't.
- You learn from every trade. After every trade, you write a memory entry.

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
            "I'm *Oniṣòwò* — Yoruba for *merchant*.\n\n"
            "I trade crypto on Bitget, powered by *Qwen 3.6 Plus*. "
            "I have 100+ skills, MEV awareness, sybil scoring, "
            "and a memory that learns from every trade.\n\n"
            "*Quick start:*\n"
            "• `/status` — your portfolio + P&L\n"
            "• `/buy SOL 100` — buy $100 of SOL (Qwen reasons through it first)\n"
            "• `/sell BTC 50` — sell $50 of BTC\n"
            "• `/skills` — list my 100+ skills\n"
            "• `/journal` — recent trade journal with Qwen's reasoning\n"
            "• `/llm` — confirm I'm running on Qwen 3.6 Plus\n"
            "• `/help` — full command list\n\n"
            "Your keys never leave your machine. I'm a self-hostable open-source bot. "
            "Built for the [Bitget AI Base Camp Hackathon S1](https://bitget-ai.gitbook.io/base-camp-hackathon-s1-en)."
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
            total_value = self.bitget.get_portfolio_value_usdt()
            cash = self.bitget.get_account_balance("USDT")
            positions = self.bitget.get_positions()

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
        """Free-form question. Let Qwen handle it with available skills."""
        try:
            # Build context
            balance = self.bitget.get_account_balance("USDT")
            portfolio = self.bitget.get_portfolio_value_usdt()

            context_msg = (
                f"Current context:\n"
                f"- USDT balance: ${balance:.2f}\n"
                f"- Portfolio value: ${portfolio:.2f}\n"
                f"- User said: {ctx.user_message}\n"
            )

            # Use Qwen to interpret
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

            # If Qwen wants to call a tool, do it
            if resp.get("tool_calls"):
                # Process the first tool call
                tool_call = resp["tool_calls"][0]
                skill_name = tool_call["function"]["name"]
                skill_args_str = tool_call["function"]["arguments"]
                try:
                    skill_args = json.loads(skill_args_str)
                except Exception:
                    skill_args = {}

                result = self.skills.invoke(skill_name, skill_args)

                # Get Qwen's final answer with the tool result
                followup = self.qwen.chat(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": ctx.user_message},
                        {"role": "assistant", "content": resp["content"] or "(invoking skill)"},
                        {"role": "tool", "tool_call_id": tool_call.get("id", "1"), "name": skill_name, "content": json.dumps(result)},
                    ],
                    max_tokens=1000,
                )
                return followup["content"]

            return resp["content"] or "🤔 I understood, but I'm not sure what to do. Try `/help` for commands."
        except Exception as e:
            logger.exception(f"_cmd_ask failed: {e}")
            return f"❌ I couldn't process that: {e}"

    # -------------------------------------------------------------------------
    # Trade execution
    # -------------------------------------------------------------------------

    def _handle_trade(self, ctx: AgentContext, side: str) -> str:
        symbol = (ctx.args.get("symbol") or "").upper()
        amount_usd = ctx.args.get("amount_usd") or 0

        if not symbol or amount_usd <= 0:
            return f"❌ Usage: `/{side} SYMBOL USDT_AMOUNT`\n\nExample: `/{side} SOL 100`"

        # Normalize symbol (e.g., SOL → SOLUSDT)
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        try:
            # PERCEIVE
            balance = self.bitget.get_account_balance("USDT")
            portfolio = self.bitget.get_portfolio_value_usdt()
            ticker = self.bitget.get_ticker(symbol)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            price = float(ticker.get("lastPr", 0))
            open_positions = len(self.db.get_open_trades())

            if price <= 0:
                return f"❌ Couldn't get price for {symbol}. Symbol might be invalid."

            # RISK CHECK
            allowed, reason = self.risk.check_order(
                symbol=symbol,
                side=side,
                size_usd=amount_usd,
                portfolio_value_usd=portfolio,
                open_positions_count=open_positions,
            )

            if not allowed:
                return f"🛑 *Trade blocked by risk engine:*\n\n{reason}"

            # DECIDE — ask Qwen for reasoning
            try:
                decision_prompt = (
                    f"Trade request: {side.upper()} {amount_usd:.2f} USDT of {symbol} at ${price:.4f}\n"
                    f"Portfolio: ${portfolio:.2f} (USDT: ${balance:.2f})\n"
                    f"Open positions: {open_positions}\n\n"
                    f"In 1-2 sentences, give the reasoning for this trade. "
                    f"Be concise. No hype."
                )
                decision_resp = self.qwen.chat(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": decision_prompt},
                    ],
                    max_tokens=200,
                )
                reasoning = decision_resp["content"]
            except Exception:
                reasoning = "(reasoning unavailable)"

            # EXECUTE
            try:
                order = self.bitget.place_spot_order(
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    quote_size=str(amount_usd) if side == "buy" else None,
                )
                order_id = order.get("orderId", "")

                # Record the trade
                size = amount_usd / price if price > 0 else 0
                trade_id = self.db.record_trade(
                    symbol=symbol,
                    side=side,
                    order_type="spot",
                    size=size,
                    price=price,
                    quote_usd=amount_usd,
                    order_id=order_id,
                    reason=reasoning,
                    skills_used=["place_spot_order", "get_ticker", "risk_check", "qwen_reasoning"],
                    confidence=0.7,
                )

                # Record the signal
                self.db.record_signal(
                    symbol=symbol,
                    action=side,
                    reasoning=reasoning,
                    skills_invoked=["place_spot_order", "get_ticker", "risk_check", "qwen_reasoning"],
                    market_state={"price": price, "portfolio": portfolio, "balance": balance},
                    trade_id=trade_id,
                )

                # REFLECT — add a memory entry
                self.db.add_memory(
                    "observation",
                    f"Trade: {side.upper()} ${amount_usd:.2f} of {symbol} at ${price:.4f}. "
                    f"Reason: {reasoning[:150]}",
                    tags=[side, symbol, "trade"],
                    importance=4,
                )

                return (
                    f"✅ *Trade executed*\n\n"
                    f"📋 Order ID: `{order_id}`\n"
                    f"💱 {side.upper()} ${amount_usd:.2f} of {symbol}\n"
                    f"💰 Price: `${price:.4f}`\n"
                    f"📐 Size: `{size:.6f}` {symbol.replace('USDT', '')}\n\n"
                    f"*Reasoning:* {reasoning}\n\n"
                    f"📓 Logged to journal. Use `/journal` to see it later."
                )
            except BitgetAPIError as e:
                return f"❌ Bitget rejected the order: {e}"
        except Exception as e:
            logger.exception(f"_handle_trade failed: {e}")
            return f"❌ Trade failed: {e}"
