"""
Skills Registry — the 100+ callable skills that make Oniṣòwò unique.

Each skill is a function with:
- name: snake_case
- description: what it does
- parameters: input schema
- returns: output schema
- category: tier (core_trading, risk, onchain, market_intel, etc.)

The registry exposes:
- invoke(skill_name, args): runs a skill
- list_skills_for_display(): pretty list for /skills command
- get_skill_descriptions(): text for system prompt
- get_tool_schemas(): OpenAI function-calling format for Qwen
"""

import json
import logging
import time
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from clients.bitget import BitgetClient, BitgetAPIError
from clients.qwen import QwenClient
from db.database import Database
from risk.engine import RiskEngine

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """A single skill — a callable function with metadata."""
    name: str
    description: str
    category: str
    func: Callable
    parameters: dict = field(default_factory=dict)


class SkillsRegistry:
    """The 100+ skills registry. Organizes all skills by tier."""

    def __init__(self, bitget: BitgetClient, db: Database, risk: RiskEngine, qwen: QwenClient):
        self.bitget = bitget
        self.db = db
        self.risk = risk
        self.qwen = qwen

        self.skills: dict[str, Skill] = {}
        self._register_all()

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def _register_all(self):
        """Register all 100+ skills organized by tier."""
        # Tier 1: Core trading (Bitget API wrappers) - 15
        self._register(Skill("place_spot_order", "Place a spot market/limit order on Bitget", "core_trading", self._s_place_spot_order, {"symbol": "str", "side": "str", "size_usd": "float"}))
        self._register(Skill("cancel_order", "Cancel a pending order by ID", "core_trading", self._s_cancel_order, {"order_id": "str", "symbol": "str"}))
        self._register(Skill("get_balance", "Get USDT balance", "core_trading", self._s_get_balance, {}))
        self._register(Skill("get_ticker", "Get current price for a symbol", "core_trading", self._s_get_ticker, {"symbol": "str"}))
        self._register(Skill("get_orderbook", "Get order book depth for a symbol", "core_trading", self._s_get_orderbook, {"symbol": "str", "limit": "int"}))
        self._register(Skill("get_candles", "Get OHLCV candles for technical analysis", "core_trading", self._s_get_candles, {"symbol": "str", "granularity": "str", "limit": "int"}))
        self._register(Skill("get_open_orders", "List all pending orders", "core_trading", self._s_get_open_orders, {}))
        self._register(Skill("get_positions", "List all open futures positions", "core_trading", self._s_get_positions, {}))
        self._register(Skill("place_futures_order", "Place a futures (perps) order", "core_trading", self._s_place_futures_order, {"symbol": "str", "side": "str", "size": "float", "leverage": "int"}))
        self._register(Skill("cancel_all_orders", "Cancel all open orders (panic button)", "core_trading", self._s_cancel_all_orders, {}))
        self._register(Skill("get_account_summary", "Full account summary (all assets + USDT value)", "core_trading", self._s_get_account_summary, {}))
        self._register(Skill("get_funding_rate", "Get current funding rate for a futures symbol", "core_trading", self._s_get_funding_rate, {"symbol": "str"}))
        self._register(Skill("get_24h_stats", "24h high/low/volume/change for a symbol", "core_trading", self._s_get_24h_stats, {"symbol": "str"}))
        self._register(Skill("set_leverage", "Set leverage for a futures symbol (max 2x in our config)", "core_trading", self._s_set_leverage, {"symbol": "str", "leverage": "int"}))
        self._register(Skill("get_trade_history", "Recent trade history (last N orders)", "core_trading", self._s_get_trade_history, {"symbol": "str", "limit": "int"}))

        # Tier 2: Risk & safety - 12
        self._register(Skill("risk_check_order", "Run an order through the risk engine (no execution)", "risk", self._s_risk_check, {"symbol": "str", "side": "str", "size_usd": "float"}))
        self._register(Skill("get_risk_status", "Show current risk engine configuration", "risk", self._s_risk_status, {}))
        self._register(Skill("activate_kill_switch", "Halt all trading (user can release via /release)", "risk", self._s_kill, {"reason": "str"}))
        self._register(Skill("check_drawdown", "Check current drawdown against peak", "risk", self._s_check_drawdown, {}))
        self._register(Skill("update_max_trade", "Change max trade size (per session)", "risk", self._s_update_max_trade, {"new_max": "float"}))
        self._register(Skill("update_max_drawdown", "Change max drawdown % (per session)", "risk", self._s_update_max_dd, {"new_max_pct": "float"}))
        self._register(Skill("position_size_calc", "Calculate optimal position size given risk %", "risk", self._s_position_size, {"entry": "float", "stop": "float", "risk_pct": "float", "portfolio": "float"}))
        self._register(Skill("exposure_check", "Check total portfolio exposure (concentration risk)", "risk", self._s_exposure_check, {}))
        self._register(Skill("correlation_check", "Check correlation between two assets (avoid over-exposure)", "risk", self._s_correlation, {"symbol_a": "str", "symbol_b": "str"}))
        self._register(Skill("daily_pnl_check", "Get today's realized P&L from journal", "risk", self._s_daily_pnl, {}))
        self._register(Skill("whitelist_symbol", "Add a symbol to the whitelist (override blacklist)", "risk", self._s_whitelist, {"symbol": "str"}))
        self._register(Skill("blacklist_symbol", "Add a symbol to the blacklist", "risk", self._s_blacklist, {"symbol": "str"}))

        # Tier 3: Onchain intelligence - 20
        self._register(Skill("mev_exposure_check", "Check MEV exposure for a token (sandwich attack risk)", "onchain", self._s_mev_check, {"token": "str"}))
        self._register(Skill("sybil_score", "Score a wallet for sybil risk (0-100, lower = safer)", "onchain", self._s_sybil_score, {"wallet": "str"}))
        self._register(Skill("holder_concentration", "Check holder concentration (top-10 %)", "onchain", self._s_holder_conc, {"token": "str"}))
        self._register(Skill("contract_safety", "Quick contract safety check (verified? proxy? owner?)", "onchain", self._s_contract_safety, {"address": "str", "chain": "str"}))
        self._register(Skill("recent_large_txs", "Get recent large transactions for a token", "onchain", self._s_recent_txs, {"token": "str", "min_usd": "float"}))
        self._register(Skill("wallet_age", "How old is this wallet? (days since first tx)", "onchain", self._s_wallet_age, {"wallet": "str"}))
        self._register(Skill("wallet_funding_source", "Trace the funding source of a wallet", "onchain", self._s_funding_source, {"wallet": "str"}))
        self._register(Skill("approval_check", "Check ERC20 approvals for a wallet (security)", "onchain", self._s_approval_check, {"wallet": "str"}))
        self._register(Skill("token_sniffer", "Sniff a token contract for honeypot patterns", "onchain", self._s_token_sniffer, {"address": "str", "chain": "str"}))
        self._register(Skill("lp_lock_check", "Check if LP tokens are locked (rug-pull risk)", "onchain", self._s_lp_lock, {"token": "str"}))
        self._register(Skill("top_holders", "Get top 20 holders of a token", "onchain", self._s_top_holders, {"token": "str"}))
        self._register(Skill("whale_movement_alert", "Alert if a top-10 holder moved >5% in last hour", "onchain", self._s_whale_alert, {"token": "str"}))
        self._register(Skill("deployer_history", "Check if a deployer has launched rugs before", "onchain", self._s_deployer_history, {"address": "str", "chain": "str"}))
        self._register(Skill("gas_oracle", "Current gas price (for transaction timing)", "onchain", self._s_gas_oracle, {"chain": "str"}))
        self._register(Skill("block_explorer_link", "Generate block explorer link for an address/tx", "onchain", self._s_explorer_link, {"address_or_tx": "str", "chain": "str"}))
        self._register(Skill("tx_status", "Get the status of a transaction (pending/confirmed/failed)", "onchain", self._s_tx_status, {"tx_hash": "str", "chain": "str"}))
        self._register(Skill("token_decimals_lookup", "Get decimals for a token contract", "onchain", self._s_decimals, {"address": "str", "chain": "str"}))
        self._register(Skill("total_supply_check", "Get total + circulating supply for a token", "onchain", self._s_supply, {"token": "str"}))
        self._register(Skill("mint_authority_check", "Can the team mint more tokens? (rug-pull risk)", "onchain", self._s_mint_auth, {"token": "str"}))
        self._register(Skill("recent_rugs_similar", "Check for recent rug pulls with similar characteristics", "onchain", self._s_rug_similar, {"token": "str"}))

        # Tier 4: Market intelligence - 15
        self._register(Skill("funding_rate_history", "Funding rate history for a futures symbol", "market_intel", self._s_funding_hist, {"symbol": "str", "days": "int"}))
        self._register(Skill("open_interest_delta", "Change in open interest over last N hours", "market_intel", self._s_oi_delta, {"symbol": "str", "hours": "int"}))
        self._register(Skill("long_short_ratio", "Current long/short ratio for a symbol", "market_intel", self._s_long_short, {"symbol": "str"}))
        self._register(Skill("liquidation_heatmap", "Estimate liquidation clusters at price levels", "market_intel", self._s_liq_heatmap, {"symbol": "str"}))
        self._register(Skill("fear_greed_index", "Crypto Fear & Greed Index (0-100)", "market_intel", self._s_fear_greed, {}))
        self._register(Skill("btc_dominance", "Current BTC dominance %", "market_intel", self._s_btc_dom, {}))
        self._register(Skill("market_cap", "Total crypto market cap", "market_intel", self._s_market_cap, {}))
        self._register(Skill("top_movers", "Top gainers and losers in last 24h", "market_intel", self._s_top_movers, {"limit": "int"}))
        self._register(Skill("volume_anomaly", "Detect unusual volume for a symbol (vs 7d avg)", "market_intel", self._s_vol_anomaly, {"symbol": "str"}))
        self._register(Skill("correlation_matrix", "Build correlation matrix for top assets", "market_intel", self._s_corr_matrix, {"symbols": "list", "days": "int"}))
        self._register(Skill("volatility_calc", "Calculate historical volatility (annualized)", "market_intel", self._s_volatility, {"symbol": "str", "days": "int"}))
        self._register(Skill("rsi", "Relative Strength Index (14-period)", "market_intel", self._s_rsi, {"symbol": "str", "period": "int"}))
        self._register(Skill("macd", "MACD indicator (12/26/9)", "market_intel", self._s_macd, {"symbol": "str"}))
        self._register(Skill("ema_cross", "EMA crossover signal (e.g., 9/21 cross)", "market_intel", self._s_ema_cross, {"symbol": "str", "fast": "int", "slow": "int"}))
        self._register(Skill("bollinger_bands", "Bollinger Bands (20, 2)", "market_intel", self._s_bollinger, {"symbol": "str", "period": "int"}))

        # Tier 5: Sentiment & news - 10
        self._register(Skill("news_fetch", "Fetch recent news for a token or topic", "sentiment", self._s_news, {"query": "str", "limit": "int"}))
        self._register(Skill("news_summarize", "Summarize the latest news (LLM)", "sentiment", self._s_news_summary, {"query": "str"}))
        self._register(Skill("social_sentiment", "Aggregate social sentiment for a token", "sentiment", self._s_social_sentiment, {"token": "str"}))
        self._register(Skill("sentiment_score", "Quantified sentiment score (-1 to +1)", "sentiment", self._s_sentiment_score, {"token": "str"}))
        self._register(Skill("narrative_detector", "Detect the current hot narrative (AI, RWA, memes)", "sentiment", self._s_narrative, {}))
        self._register(Skill("x_mentions", "Recent X/Twitter mentions for a token", "sentiment", self._s_x_mentions, {"token": "str", "hours": "int"}))
        self._register(Skill("reddit_sentiment", "Sentiment from crypto subreddits", "sentiment", self._s_reddit, {"token": "str"}))
        self._register(Skill("influencer_tracker", "Track what top crypto influencers are saying", "sentiment", self._s_influencers, {}))
        self._register(Skill("keyword_alert", "Alert when a keyword appears in news/X", "sentiment", self._s_keyword_alert, {"keyword": "str"}))
        self._register(Skill("macro_calendar", "Upcoming macro events (FOMC, CPI, etc.)", "sentiment", self._s_macro_cal, {"days_ahead": "int"}))

        # Tier 6: Strategy & decision - 10
        self._register(Skill("edge_estimator", "Estimate edge % for a given setup", "strategy", self._s_edge_est, {"setup": "str", "context": "dict"}))
        self._register(Skill("thesis_writer", "Write a clear, falsifiable trade thesis", "strategy", self._s_thesis, {"symbol": "str", "action": "str", "reasoning": "str"}))
        self._register(Skill("confidence_calibrator", "Calibrate my confidence based on past hits/misses", "strategy", self._s_confidence, {"pattern": "str"}))
        self._register(Skill("pattern_recognizer", "Recognize chart patterns (head&shoulders, triangles, etc.)", "strategy", self._s_pattern, {"symbol": "str"}))
        self._register(Skill("support_resistance", "Identify key support/resistance levels", "strategy", self._s_sr, {"symbol": "str"}))
        self._register(Skill("trend_strength", "Measure trend strength (ADX, slope, etc.)", "strategy", self._s_trend, {"symbol": "str"}))
        self._register(Skill("mean_reversion_signal", "Signal: is price far from mean? (z-score)", "strategy", self._s_meanrev, {"symbol": "str", "lookback": "int"}))
        self._register(Skill("momentum_signal", "Signal: momentum strength (RSI, MACD, ROC)", "strategy", self._s_momentum, {"symbol": "str"}))
        self._register(Skill("kelly_criterion", "Optimal bet size from edge + odds (Kelly formula)", "strategy", self._s_kelly, {"edge": "float", "odds": "float"}))
        self._register(Skill("strategy_backtest", "Quick backtest of a simple strategy on a symbol", "strategy", self._s_backtest, {"symbol": "str", "rule": "str", "days": "int"}))

        # Tier 7: Agent meta - 10
        self._register(Skill("recursive_improvement", "Run recursive self-improvement: review + tune", "agent_meta", self._s_recursive, {}))
        self._register(Skill("journal_writer", "Write a structured entry to the journal", "agent_meta", self._s_journal_write, {"entry": "dict"}))
        self._register(Skill("memory_recall", "Recall memories matching a query", "agent_meta", self._s_memory_recall, {"query": "str", "limit": "int"}))
        self._register(Skill("memory_store", "Store a new memory entry", "agent_meta", self._s_memory_store, {"category": "str", "content": "str", "importance": "int"}))
        self._register(Skill("prompt_tuner", "Tune the system prompt based on recent performance", "agent_meta", self._s_prompt_tune, {}))
        self._register(Skill("behavior_log", "Log my recent decision pattern for analysis", "agent_meta", self._s_behavior_log, {}))
        self._register(Skill("explain_decision", "Explain my reasoning for a past trade", "agent_meta", self._s_explain, {"trade_id": "int"}))
        self._register(Skill("decision_audit", "Audit all decisions in the last 24h", "agent_meta", self._s_audit, {"hours": "int"}))
        self._register(Skill("skill_performance", "Which skills are giving the best signal?", "agent_meta", self._s_skill_perf, {}))
        self._register(Skill("anomaly_detector", "Detect anomalies in my own behavior (e.g., overtrading)", "agent_meta", self._s_anomaly, {}))

        # Tier 8: User-facing / Telegram surface - 5
        self._register(Skill("send_alert", "Send an alert to the user", "user_facing", self._s_alert, {"message": "str", "priority": "str"}))
        self._register(Skill("ask_approval", "Ask user to approve a trade", "user_facing", self._s_ask_approval, {"trade": "dict"}))
        self._register(Skill("format_pnl", "Format P&L for display", "user_facing", self._s_format_pnl, {"pnl_usd": "float", "pnl_pct": "float"}))
        self._register(Skill("send_chart", "Send a chart image", "user_facing", self._s_chart, {"symbol": "str", "period": "str"}))
        self._register(Skill("format_reasoning", "Format agent reasoning as Telegram-friendly markdown", "user_facing", self._s_format_reasoning, {"reasoning": "str"}))

        # Tier 9: Utilities - 8+
        self._register(Skill("retry_with_backoff", "Retry a function with exponential backoff", "utility", self._s_retry, {"func": "callable", "max_attempts": "int"}))
        self._register(Skill("normalize_symbol", "Normalize a symbol (SOL → SOLUSDT)", "utility", self._s_normalize_sym, {"symbol": "str"}))
        self._register(Skill("time_now", "Current time in UTC", "utility", self._s_time, {}))
        self._register(Skill("to_usd", "Convert a coin amount to USD at current price", "utility", self._s_to_usd, {"coin": "str", "amount": "float"}))
        self._register(Skill("from_usd", "Convert USD amount to coin units at current price", "utility", self._s_from_usd, {"coin": "str", "usd": "float"}))
        self._register(Skill("cache_get", "Get a cached value (1h TTL by default)", "utility", self._s_cache_get, {"key": "str"}))
        self._register(Skill("cache_set", "Set a cached value with TTL", "utility", self._s_cache_set, {"key": "str", "value": "any", "ttl_seconds": "int"}))
        self._register(Skill("log_event", "Log a structured event for debugging", "utility", self._s_log, {"level": "str", "message": "str", "context": "dict"}))

    def _register(self, skill: Skill):
        self.skills[skill.name] = skill

    def count(self) -> int:
        return len(self.skills)

    # -------------------------------------------------------------------------
    # Invocation
    # -------------------------------------------------------------------------

    def invoke(self, name: str, args: dict) -> dict:
        """Invoke a skill by name with args. Returns the result."""
        if name not in self.skills:
            return {"error": f"Unknown skill: {name}", "available": list(self.skills.keys())[:20]}

        skill = self.skills[name]
        try:
            result = skill.func(**args)
            return {"ok": True, "skill": name, "result": result}
        except Exception as e:
            logger.exception(f"Skill {name} failed: {e}")
            return {"ok": False, "skill": name, "error": str(e)}

    def invoke_by_name(self, name: str, extra: list) -> str:
        """Invoke a skill with extra args (from /skill command). Returns display text."""
        args = {}
        # Try to parse positional args as keyword args
        if extra:
            for i, arg in enumerate(extra):
                args[f"arg{i}"] = arg

        result = self.invoke(name, args)
        if result.get("ok"):
            return f"*Skill `{name}` result:*\n\n```json\n{json.dumps(result['result'], indent=2, default=str)[:2000]}\n```"
        else:
            return f"❌ Skill `{name}` failed: {result.get('error', 'unknown error')}"

    # -------------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------------

    def list_skills_for_display(self) -> str:
        """List all skills grouped by tier, for /skills command."""
        by_category: dict[str, list[Skill]] = {}
        for skill in self.skills.values():
            by_category.setdefault(skill.category, []).append(skill)

        tier_names = {
            "core_trading": "1️⃣ Core Trading (Bitget API)",
            "risk": "2️⃣ Risk & Safety",
            "onchain": "3️⃣ Onchain Intelligence",
            "market_intel": "4️⃣ Market Intelligence",
            "sentiment": "5️⃣ Sentiment & News",
            "strategy": "6️⃣ Strategy & Decision",
            "agent_meta": "7️⃣ Agent Meta (self-improvement)",
            "user_facing": "8️⃣ User-facing",
            "utility": "9️⃣ Utilities",
        }

        lines = [f"*Oniṣòwò Skills* ({self.count()} total) 🛠️\n"]
        for cat_key, cat_name in tier_names.items():
            if cat_key in by_category:
                skills_in_cat = by_category[cat_key]
                lines.append(f"\n*{cat_name}* ({len(skills_in_cat)})")
                for s in skills_in_cat:
                    lines.append(f"  • `{s.name}` — {s.description}")
        return "\n".join(lines)

    def get_skill_descriptions(self) -> str:
        """Get all skill descriptions as a single text block (for system prompt)."""
        lines = []
        for skill in self.skills.values():
            lines.append(f"- {skill.name} ({skill.category}): {skill.description}")
        return "\n".join(lines[:50])  # Top 50 to avoid token bloat

    def get_tool_schemas(self) -> list[dict]:
        """Get OpenAI function-calling schemas for the most-used skills."""
        # Only expose the top 20 most useful skills as tools (avoid token bloat)
        top_skill_names = [
            "place_spot_order", "get_ticker", "get_balance", "get_candles",
            "risk_check_order", "mev_exposure_check", "sybil_score",
            "funding_rate_history", "rsi", "macd", "news_fetch",
            "edge_estimator", "thesis_writer", "memory_recall",
            "normalize_symbol", "from_usd", "to_usd",
        ]
        schemas = []
        for name in top_skill_names:
            if name in self.skills:
                skill = self.skills[name]
                # Build OpenAI tool schema
                properties = {}
                required = []
                for param, ptype in skill.parameters.items():
                    properties[param] = {"type": _json_type(ptype)}
                    required.append(param)

                schemas.append({
                    "type": "function",
                    "function": {
                        "name": skill.name,
                        "description": skill.description,
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                })
        return schemas

    # =========================================================================
    # Skill implementations (Tier 1: Core Trading)
    # =========================================================================

    def _s_place_spot_order(self, symbol: str, side: str, size_usd: float) -> dict:
        return self.bitget.place_spot_order(
            symbol=symbol, side=side, order_type="market",
            quote_size=str(size_usd) if side == "buy" else None,
        )

    def _s_cancel_order(self, order_id: str, symbol: str) -> dict:
        return self.bitget.cancel_order(symbol=symbol, order_id=order_id)

    def _s_get_balance(self) -> dict:
        return {"usdt": self.bitget.get_account_balance("USDT")}

    def _s_get_ticker(self, symbol: str) -> dict:
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        ticker = self.bitget.get_ticker(symbol)
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        return ticker

    def _s_get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        return self.bitget.get_orderbook(symbol=symbol, limit=limit)

    def _s_get_candles(self, symbol: str, granularity: str = "1h", limit: int = 100) -> list:
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        return self.bitget.get_candles(symbol=symbol, granularity=granularity, limit=limit)

    def _s_get_open_orders(self) -> list:
        return self.bitget.get_pending_orders()

    def _s_get_positions(self) -> list:
        return self.bitget.get_positions()

    def _s_place_futures_order(self, symbol: str, side: str, size: float, leverage: int = 1) -> dict:
        if leverage > self.risk.config.max_leverage:
            return {"error": f"Leverage {leverage}x exceeds max {self.risk.config.max_leverage}x"}
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        return self.bitget.place_futures_order(
            symbol=symbol, side=side, size=str(size), leverage=str(leverage)
        )

    def _s_cancel_all_orders(self) -> dict:
        orders = self.bitget.get_pending_orders()
        cancelled = []
        for order in orders:
            try:
                self.bitget.cancel_order(symbol=order["symbol"], order_id=order["orderId"])
                cancelled.append(order["orderId"])
            except Exception:
                pass
        return {"cancelled_count": len(cancelled), "cancelled_ids": cancelled}

    def _s_get_account_summary(self) -> dict:
        return {
            "total_value_usd": self.bitget.get_portfolio_value_usdt(),
            "usdt_balance": self.bitget.get_account_balance("USDT"),
            "open_positions": self.bitget.get_positions(),
        }

    def _s_get_funding_rate(self, symbol: str) -> dict:
        # Use mix API for funding rate
        try:
            return self.bitget._request("GET", "/api/v2/mix/market/ticker",
                params={"symbol": symbol, "productType": "USDT-FUTURES"})
        except Exception as e:
            return {"error": str(e)}

    def _s_get_24h_stats(self, symbol: str) -> dict:
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        ticker = self.bitget.get_ticker(symbol)
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        return {
            "symbol": symbol,
            "last": float(ticker.get("lastPr", 0)),
            "change_24h_pct": float(ticker.get("change24h", 0)),
            "high_24h": float(ticker.get("high24h", 0)),
            "low_24h": float(ticker.get("low24h", 0)),
            "volume_24h": float(ticker.get("baseVolume", 0)),
            "turnover_24h": float(ticker.get("quoteVolume", 0)),
        }

    def _s_set_leverage(self, symbol: str, leverage: int) -> dict:
        if leverage > self.risk.config.max_leverage:
            return {"error": f"Leverage {leverage}x exceeds max {self.risk.config.max_leverage}x"}
        return self.bitget.set_leverage(symbol=symbol, leverage=str(leverage))

    def _s_get_trade_history(self, symbol: str, limit: int = 50) -> list:
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        return self.bitget.get_order_history(symbol=symbol, limit=limit)

    # =========================================================================
    # Tier 2: Risk & Safety
    # =========================================================================

    def _s_risk_check(self, symbol: str, side: str, size_usd: float) -> dict:
        portfolio = self.bitget.get_portfolio_value_usdt()
        open_count = len(self.db.get_open_trades())
        allowed, reason = self.risk.check_order(
            symbol=symbol, side=side, size_usd=size_usd,
            portfolio_value_usd=portfolio, open_positions_count=open_count,
        )
        return {"allowed": allowed, "reason": reason}

    def _s_risk_status(self) -> dict:
        return self.risk.get_status()

    def _s_kill(self, reason: str = "Manual") -> dict:
        self.risk.activate_kill_switch(reason=reason)
        return {"kill_switch": "active", "reason": reason}

    def _s_check_drawdown(self) -> dict:
        portfolio = self.bitget.get_portfolio_value_usdt()
        # Get peak from snapshots
        history = self.db.get_portfolio_history(days=30)
        peak = max((h["total_value_usd"] for h in history), default=portfolio)
        safe, status = self.risk.check_drawdown(current_value=portfolio, peak_value=peak)
        return {"current": portfolio, "peak": peak, "safe": safe, "status": status}

    def _s_update_max_trade(self, new_max: float) -> dict:
        self.risk.update_limits(max_trade_usd=new_max)
        return {"new_max_trade_usd": new_max}

    def _s_update_max_dd(self, new_max_pct: float) -> dict:
        self.risk.update_limits(max_drawdown_pct=new_max_pct / 100)
        return {"new_max_drawdown_pct": new_max_pct}

    def _s_position_size(self, entry: float, stop: float, risk_pct: float, portfolio: float) -> dict:
        """Position size calc: risk_amount / (entry - stop)."""
        if entry <= stop or stop <= 0:
            return {"error": "Invalid entry/stop"}
        risk_amount = portfolio * (risk_pct / 100)
        size_units = risk_amount / (entry - stop)
        size_usd = size_units * entry
        return {
            "entry": entry, "stop": stop, "risk_pct": risk_pct,
            "portfolio": portfolio, "risk_amount_usd": risk_amount,
            "size_units": round(size_units, 6), "size_usd": round(size_usd, 2),
        }

    def _s_exposure_check(self) -> dict:
        positions = self.db.get_open_trades()
        total = sum(p.get("quote_usd", 0) for p in positions)
        portfolio = self.bitget.get_portfolio_value_usdt()
        return {
            "open_positions": len(positions),
            "total_exposure_usd": total,
            "portfolio_usd": portfolio,
            "exposure_pct": (total / portfolio * 100) if portfolio > 0 else 0,
        }

    def _s_correlation(self, symbol_a: str, symbol_b: str) -> dict:
        # Simple correlation: returns 0.0 placeholder (would need price history)
        return {"symbol_a": symbol_a, "symbol_b": symbol_b, "correlation": 0.0, "note": "Simplified (no price history cached)"}

    def _s_daily_pnl(self) -> dict:
        trades = self.db.get_trades_for_review(days=1)
        pnl = sum(t.get("pnl_usd", 0) for t in trades)
        return {"date": time.strftime("%Y-%m-%d"), "pnl_usd": pnl, "trade_count": len(trades)}

    def _s_whitelist(self, symbol: str) -> dict:
        # Implementation: clear from blacklist
        if symbol in self.risk.config.blacklist_symbols:
            self.risk.config.blacklist_symbols = tuple(
                s for s in self.risk.config.blacklist_symbols if s != symbol
            )
        return {"whitelisted": symbol, "current_blacklist": list(self.risk.config.blacklist_symbols)}

    def _s_blacklist(self, symbol: str) -> dict:
        if symbol not in self.risk.config.blacklist_symbols:
            self.risk.config.blacklist_symbols = self.risk.config.blacklist_symbols + (symbol,)
        return {"blacklisted": symbol, "current_blacklist": list(self.risk.config.blacklist_symbols)}

    # =========================================================================
    # Tier 3: Onchain intelligence (stubs/heuristics — full impl needs RPC)
    # =========================================================================

    def _s_mev_check(self, token: str) -> dict:
        """Heuristic MEV exposure score based on token liquidity (proxy)."""
        try:
            if not token.endswith("USDT"):
                sym = token + "USDT"
            else:
                sym = token
            ticker = self.bitget.get_ticker(sym)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            vol = float(ticker.get("baseVolume", 0))
            # Higher volume = more MEV bots = higher exposure
            if vol > 1_000_000_000:
                exposure = "HIGH"
            elif vol > 100_000_000:
                exposure = "MEDIUM"
            else:
                exposure = "LOW"
            return {"token": token, "volume_24h": vol, "mev_exposure": exposure, "advice": "Use private mempool for large swaps" if exposure == "HIGH" else "OK"}
        except Exception as e:
            return {"error": str(e)}

    def _s_sybil_score(self, wallet: str) -> dict:
        """Placeholder sybil score. Real implementation needs funding source analysis."""
        return {
            "wallet": wallet,
            "sybil_score": 50,
            "confidence": 0.3,
            "note": "Heuristic only. Real implementation traces funding sources.",
        }

    def _s_holder_conc(self, token: str) -> dict:
        return {"token": token, "top_10_pct": 0.0, "note": "Stub (needs on-chain holder query)"}

    def _s_contract_safety(self, address: str, chain: str = "eth") -> dict:
        return {"address": address, "chain": chain, "verified": None, "note": "Stub (needs contract verification API)"}

    def _s_recent_txs(self, token: str, min_usd: float = 10000) -> dict:
        return {"token": token, "large_tx_count_24h": 0, "note": "Stub (needs on-chain tx scan)"}

    def _s_wallet_age(self, wallet: str) -> dict:
        return {"wallet": wallet, "age_days": None, "note": "Stub (needs first-tx query)"}

    def _s_funding_source(self, wallet: str) -> dict:
        return {"wallet": wallet, "funding_source": None, "note": "Stub (needs tx trace)"}

    def _s_approval_check(self, wallet: str) -> dict:
        return {"wallet": wallet, "active_approvals": 0, "note": "Stub (needs approval indexer)"}

    def _s_token_sniffer(self, address: str, chain: str = "eth") -> dict:
        return {"address": address, "is_honeypot": None, "note": "Stub (needs contract simulation)"}

    def _s_lp_lock(self, token: str) -> dict:
        return {"token": token, "lp_locked_pct": 0, "note": "Stub (needs LP locker query)"}

    def _s_top_holders(self, token: str) -> dict:
        return {"token": token, "top_holders": [], "note": "Stub (needs holder indexer)"}

    def _s_whale_alert(self, token: str) -> dict:
        return {"token": token, "alert": False, "note": "Stub (needs real-time whale watcher)"}

    def _s_deployer_history(self, address: str, chain: str = "eth") -> dict:
        return {"address": address, "deployed_tokens": 0, "rugs": 0, "note": "Stub (needs deployer history)"}

    def _s_gas_oracle(self, chain: str = "eth") -> dict:
        return {"chain": chain, "gas_price_gwei": None, "note": "Stub (needs gas oracle API)"}

    def _s_explorer_link(self, address_or_tx: str, chain: str = "eth") -> dict:
        explorers = {
            "eth": "https://etherscan.io",
            "bsc": "https://bscscan.com",
            "solana": "https://solscan.io",
        }
        base = explorers.get(chain, "https://etherscan.io")
        return {"url": f"{base}/{'tx' if len(address_or_tx) > 50 else 'address'}/{address_or_tx}"}

    def _s_tx_status(self, tx_hash: str, chain: str = "eth") -> dict:
        return {"tx_hash": tx_hash, "status": None, "note": "Stub (needs RPC)"}

    def _s_decimals(self, address: str, chain: str = "eth") -> dict:
        return {"address": address, "decimals": 18, "note": "Stub (default 18)"}

    def _s_supply(self, token: str) -> dict:
        return {"token": token, "total_supply": 0, "circulating": 0, "note": "Stub"}

    def _s_mint_auth(self, token: str) -> dict:
        return {"token": token, "can_mint": None, "note": "Stub (needs contract ABI check)"}

    def _s_rug_similar(self, token: str) -> dict:
        return {"token": token, "similar_rugs": [], "note": "Stub (needs rug database)"}

    # =========================================================================
    # Tier 4: Market intelligence
    # =========================================================================

    def _s_funding_hist(self, symbol: str, days: int = 7) -> dict:
        try:
            return self.bitget._request("GET", "/api/v2/mix/market/history-fund-rate",
                params={"symbol": symbol, "productType": "USDT-FUTURES", "pageSize": str(days * 3)})
        except Exception as e:
            return {"error": str(e)}

    def _s_oi_delta(self, symbol: str, hours: int = 24) -> dict:
        return {"symbol": symbol, "hours": hours, "oi_change_pct": 0, "note": "Stub (needs OI history)"}

    def _s_long_short(self, symbol: str) -> dict:
        return {"symbol": symbol, "long_pct": 50, "short_pct": 50, "note": "Stub"}

    def _s_liq_heatmap(self, symbol: str) -> dict:
        return {"symbol": symbol, "liq_levels": [], "note": "Stub (needs OI + funding data)"}

    def _s_fear_greed(self) -> dict:
        return {"value": 50, "label": "Neutral", "note": "Stub (needs fear&greed API)"}

    def _s_btc_dom(self) -> dict:
        return {"btc_dominance_pct": 50.0, "note": "Stub"}

    def _s_market_cap(self) -> dict:
        return {"total_market_cap_usd": 0, "note": "Stub"}

    def _s_top_movers(self, limit: int = 10) -> dict:
        return {"gainers": [], "losers": [], "note": "Stub"}

    def _s_vol_anomaly(self, symbol: str) -> dict:
        return {"symbol": symbol, "is_anomalous": False, "note": "Stub"}

    def _s_corr_matrix(self, symbols: list, days: int = 30) -> dict:
        return {"symbols": symbols, "matrix": [], "note": "Stub"}

    def _s_volatility(self, symbol: str, days: int = 30) -> dict:
        return {"symbol": symbol, "volatility_annualized": 0, "note": "Stub"}

    def _s_rsi(self, symbol: str, period: int = 14) -> dict:
        # Simplified: would need to fetch candles and calculate
        return {"symbol": symbol, "rsi": 50, "note": "Stub (needs candle data)"}

    def _s_macd(self, symbol: str) -> dict:
        return {"symbol": symbol, "macd": 0, "signal": 0, "histogram": 0, "note": "Stub"}

    def _s_ema_cross(self, symbol: str, fast: int = 9, slow: int = 21) -> dict:
        return {"symbol": symbol, "fast": fast, "slow": slow, "signal": "neutral", "note": "Stub"}

    def _s_bollinger(self, symbol: str, period: int = 20) -> dict:
        return {"symbol": symbol, "upper": 0, "middle": 0, "lower": 0, "note": "Stub"}

    # =========================================================================
    # Tier 5: Sentiment & news
    # =========================================================================

    def _s_news(self, query: str, limit: int = 10) -> dict:
        return {"query": query, "articles": [], "note": "Stub (needs news API)"}

    def _s_news_summary(self, query: str) -> dict:
        return {"query": query, "summary": "(stub) - would call news_fetch + LLM summarize"}

    def _s_social_sentiment(self, token: str) -> dict:
        return {"token": token, "sentiment": 0.0, "note": "Stub"}

    def _s_sentiment_score(self, token: str) -> dict:
        return {"token": token, "score": 0.0, "scale": "-1 to +1", "note": "Stub"}

    def _s_narrative(self) -> dict:
        return {"hot_narratives": ["AI", "RWA", "Memecoins"], "note": "Stub"}

    def _s_x_mentions(self, token: str, hours: int = 24) -> dict:
        return {"token": token, "mentions": 0, "hours": hours, "note": "Stub"}

    def _s_reddit(self, token: str) -> dict:
        return {"token": token, "sentiment": 0.0, "note": "Stub"}

    def _s_influencers(self) -> dict:
        return {"recent_calls": [], "note": "Stub"}

    def _s_keyword_alert(self, keyword: str) -> dict:
        return {"keyword": keyword, "alert_active": True, "note": "Stub"}

    def _s_macro_cal(self, days_ahead: int = 7) -> dict:
        return {"events": [], "note": "Stub (needs macro calendar API)"}

    # =========================================================================
    # Tier 6: Strategy & decision
    # =========================================================================

    def _s_edge_est(self, setup: str, context: dict) -> dict:
        return {"setup": setup, "edge_pct": 5.0, "note": "Stub (would use historical win rate)"}

    def _s_thesis(self, symbol: str, action: str, reasoning: str) -> dict:
        prompt = f"Write a clear, falsifiable trade thesis for {action} {symbol}.\n\nContext: {reasoning}\n\nFormat: 1-line claim, 3 bullets (why now, what invalidates, target)"
        resp = self.qwen.chat(messages=[{"role": "user", "content": prompt}], max_tokens=200)
        return {"symbol": symbol, "action": action, "thesis": resp["content"]}

    def _s_confidence(self, pattern: str) -> dict:
        return {"pattern": pattern, "calibrated_confidence": 0.5, "note": "Stub (needs past hit rate)"}

    def _s_pattern(self, symbol: str) -> dict:
        return {"symbol": symbol, "patterns": [], "note": "Stub"}

    def _s_sr(self, symbol: str) -> dict:
        return {"symbol": symbol, "support": [], "resistance": [], "note": "Stub"}

    def _s_trend(self, symbol: str) -> dict:
        return {"symbol": symbol, "trend": "sideways", "strength": 0, "note": "Stub"}

    def _s_meanrev(self, symbol: str, lookback: int = 20) -> dict:
        return {"symbol": symbol, "z_score": 0, "signal": "neutral", "note": "Stub"}

    def _s_momentum(self, symbol: str) -> dict:
        return {"symbol": symbol, "momentum": 0, "signal": "neutral", "note": "Stub"}

    def _s_kelly(self, edge: float, odds: float) -> dict:
        """Kelly criterion: f* = (bp - q) / b, where b=odds-1, p=win_prob, q=1-p"""
        if odds <= 1:
            return {"error": "Odds must be > 1"}
        # Assume edge is in %, convert to probability
        p = 0.5 + (edge / 200)  # Rough: edge of 10% = 55% prob
        q = 1 - p
        b = odds - 1
        kelly = (b * p - q) / b
        return {"edge_pct": edge, "odds": odds, "win_prob": p, "kelly_pct": round(kelly * 100, 2)}

    def _s_backtest(self, symbol: str, rule: str, days: int = 90) -> dict:
        return {"symbol": symbol, "rule": rule, "days": days, "trades": 0, "win_rate": 0, "pnl": 0, "note": "Stub (needs candle data + rule parser)"}

    # =========================================================================
    # Tier 7: Agent meta
    # =========================================================================

    def _s_recursive(self) -> dict:
        """Run a recursive self-improvement cycle."""
        trades = self.db.get_trades_for_review(days=7)
        if not trades:
            return {"ok": False, "note": "No closed trades to review yet"}

        # Build summary
        wins = [t for t in trades if t.get("pnl_usd", 0) > 0]
        losses = [t for t in trades if t.get("pnl_usd", 0) < 0]
        prompt = (
            f"You are Oniṣòwò, doing recursive self-improvement.\n\n"
            f"Last 7 days: {len(trades)} trades, {len(wins)} wins, {len(losses)} losses.\n"
            f"Trades:\n"
            + "\n".join(f"- {t['side']} {t['symbol']} ${t['quote_usd']:.2f} pnl=${t.get('pnl_usd', 0):+.2f}" for t in trades)
            + "\n\nWhat 1 rule should I add to my memory to improve next week?"
        )
        resp = self.qwen.chat(messages=[{"role": "user", "content": prompt}], max_tokens=300)
        rule = resp["content"]

        # Save as a memory entry
        self.db.add_memory("rule", rule, tags=["self_improvement", "weekly"], importance=8)

        return {"ok": True, "new_rule": rule, "trades_reviewed": len(trades)}

    def _s_journal_write(self, entry: dict) -> dict:
        self.db.add_memory("observation", entry.get("content", ""), tags=entry.get("tags", []), importance=entry.get("importance", 5))
        return {"ok": True}

    def _s_memory_recall(self, query: str, limit: int = 10) -> dict:
        # Simple substring match
        all_mems = self.db.get_memories(limit=100)
        matched = [m for m in all_mems if query.lower() in m["content"].lower()][:limit]
        return {"query": query, "matches": [{"content": m["content"], "category": m["category"]} for m in matched]}

    def _s_memory_store(self, category: str, content: str, importance: int = 5) -> dict:
        mem_id = self.db.add_memory(category, content, importance=importance)
        return {"ok": True, "memory_id": mem_id}

    def _s_prompt_tune(self) -> dict:
        return {"ok": True, "note": "Stub (would rewrite system prompt based on perf)"}

    def _s_behavior_log(self) -> dict:
        return {"ok": True, "recent_decisions": [], "note": "Stub"}

    def _s_explain(self, trade_id: int) -> dict:
        trades = self.db.get_recent_trades(limit=200)
        for t in trades:
            if t["id"] == trade_id:
                return {"trade_id": trade_id, "reasoning": t.get("reason", "(no reason recorded)")}
        return {"error": f"Trade {trade_id} not found"}

    def _s_audit(self, hours: int = 24) -> dict:
        return {"hours": hours, "decisions": [], "note": "Stub"}

    def _s_skill_perf(self) -> dict:
        return {"ok": True, "best_skills": [], "note": "Stub (needs skill -> outcome correlation)"}

    def _s_anomaly(self) -> dict:
        return {"ok": True, "anomalies": [], "note": "Stub"}

    # =========================================================================
    # Tier 8: User-facing
    # =========================================================================

    def _s_alert(self, message: str, priority: str = "normal") -> dict:
        logger.info(f"ALERT [{priority}]: {message}")
        return {"ok": True, "sent": message}

    def _s_ask_approval(self, trade: dict) -> dict:
        return {"ok": True, "approval_requested": trade, "note": "Agent would use Telegram inline buttons here"}

    def _s_format_pnl(self, pnl_usd: float, pnl_pct: float) -> dict:
        sign = "+" if pnl_usd >= 0 else ""
        emoji = "🟢" if pnl_usd >= 0 else "🔴"
        return {"formatted": f"{emoji} {sign}${pnl_usd:.2f} ({sign}{pnl_pct:.2f}%)"}

    def _s_chart(self, symbol: str, period: str = "1d") -> dict:
        return {"symbol": symbol, "period": period, "note": "Stub (would generate chart image)"}

    def _s_format_reasoning(self, reasoning: str) -> dict:
        return {"formatted": f"💭 *Reasoning:*\n{reasoning}"}

    # =========================================================================
    # Tier 9: Utilities
    # =========================================================================

    def _s_retry(self, func: Optional[Callable] = None, max_attempts: int = 3) -> dict:
        return {"ok": True, "note": "Use tenacity decorator in real code"}

    def _s_normalize_sym(self, symbol: str) -> dict:
        s = symbol.upper().strip()
        if not s.endswith("USDT") and not s.endswith("USDC"):
            s = s + "USDT"
        return {"normalized": s}

    def _s_time(self) -> dict:
        return {"utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}

    def _s_to_usd(self, coin: str, amount: float) -> dict:
        try:
            sym = coin.upper() + "USDT" if not coin.upper().endswith("USDT") else coin.upper()
            ticker = self.bitget.get_ticker(sym)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            price = float(ticker.get("lastPr", 0))
            return {"coin": coin, "amount": amount, "usd_value": amount * price}
        except Exception as e:
            return {"error": str(e)}

    def _s_from_usd(self, coin: str, usd: float) -> dict:
        try:
            sym = coin.upper() + "USDT" if not coin.upper().endswith("USDT") else coin.upper()
            ticker = self.bitget.get_ticker(sym)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            price = float(ticker.get("lastPr", 0))
            return {"coin": coin, "usd": usd, "coin_amount": usd / price if price > 0 else 0}
        except Exception as e:
            return {"error": str(e)}

    def _s_cache_get(self, key: str) -> dict:
        return {"key": key, "value": None, "note": "Stub (in-memory cache would go here)"}

    def _s_cache_set(self, key: str, value: Any, ttl_seconds: int = 3600) -> dict:
        return {"key": key, "set": True, "ttl": ttl_seconds, "note": "Stub"}

    def _s_log(self, level: str, message: str, context: dict = None) -> dict:
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn(f"{message} | context={context}")
        return {"logged": True}


def _json_type(py_type_str: str) -> str:
    """Map our simple type strings to JSON Schema types."""
    mapping = {
        "str": "string",
        "int": "integer",
        "float": "number",
        "bool": "boolean",
        "list": "array",
        "dict": "object",
        "any": "string",
        "callable": "string",
    }
    return mapping.get(py_type_str, "string")
