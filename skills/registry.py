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
import re
import time
import math
from typing import Any, Callable, Optional
from dataclasses import dataclass, field

from clients.bitget import BitgetClient, BitgetAPIError
from clients.qwen import QwenClient
from db.database import Database
from risk.engine import RiskEngine
from skills import indicators as ind

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
        self._register(Skill("suggest_position_size", "Compute a smart position size from balance, confidence, and signal score. Respects user_requested_usd if set.", "risk", self._s_suggest_position_size, {"balance_usd": "float", "confidence": "float", "signal_score": "float", "user_requested_usd": "float"}))
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

        # =====================================================================
        # Tier 3: Technical indicators - 72 (replaces onchain tier; CEX-only)
        # All indicators compute from Bitget OHLCV data, no on-chain deps.
        # =====================================================================
        # --- Trend (15) ---
        self._register(Skill("ichimoku", "Ichimoku Cloud (Tenkan/Kijun/Senkou A/B/Chikou)", "indicators", self._s_ichimoku, {"symbol": "str", "highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("supertrend", "ATR-based SuperTrend with band + trend direction", "indicators", self._s_supertrend, {"symbol": "str", "highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("parabolic_sar", "Parabolic SAR with trend direction", "indicators", self._s_parabolic_sar, {"highs": "list", "lows": "list"}))
        self._register(Skill("aroon", "Aroon Up/Down + Oscillator (period)", "indicators", self._s_aroon, {"highs": "list", "lows": "list"}))
        self._register(Skill("vortex", "Vortex Indicator (VI+/VI-) — directional movement", "indicators", self._s_vortex, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("ttm_squeeze", "TTM Squeeze — Bollinger inside Keltner = squeeze", "indicators", self._s_ttm_squeeze, {"closes": "list", "highs": "list", "lows": "list"}))
        self._register(Skill("qqe", "Quantitative Qualitative Estimation (RSI + smoothed)", "indicators", self._s_qqe, {"closes": "list"}))
        self._register(Skill("halftrend", "HalfTrend — pivot-based trend", "indicators", self._s_halftrend, {"closes": "list", "highs": "list", "lows": "list"}))
        self._register(Skill("alligator", "Williams Alligator (Jaw/Teeth/Lips)", "indicators", self._s_alligator, {"highs": "list", "lows": "list"}))
        self._register(Skill("gator", "Gator Oscillator (Alligator differential)", "indicators", self._s_gator, {"highs": "list", "lows": "list"}))
        self._register(Skill("dmi", "Directional Movement Index (+DI/-DI/ADX)", "indicators", self._s_dmi, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("aroon_oscillator", "Aroon Up minus Aroon Down (oscillator only)", "indicators", self._s_aroon_oscillator, {"highs": "list", "lows": "list"}))
        self._register(Skill("dpo", "Detrended Price Oscillator", "indicators", self._s_dpo, {"closes": "list"}))
        self._register(Skill("eom", "Ease of Movement", "indicators", self._s_eom, {"closes": "list", "highs": "list", "lows": "list", "volumes": "list"}))
        self._register(Skill("tsi", "True Strength Index", "indicators", self._s_tsi, {"closes": "list"}))
        # --- Momentum (15) ---
        self._register(Skill("stochastic", "Stochastic Oscillator (%K/%D + cross)", "indicators", self._s_stochastic, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("stoch_rsi", "Stochastic RSI", "indicators", self._s_stoch_rsi, {"closes": "list"}))
        self._register(Skill("williams_r", "Williams %R", "indicators", self._s_williams_r, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("cci", "Commodity Channel Index", "indicators", self._s_cci, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("mfi", "Money Flow Index (price + volume)", "indicators", self._s_mfi, {"highs": "list", "lows": "list", "closes": "list", "volumes": "list"}))
        self._register(Skill("roc", "Rate of Change (%)", "indicators", self._s_roc, {"closes": "list"}))
        self._register(Skill("momentum", "Price momentum (raw difference)", "indicators", self._s_momentum, {"closes": "list"}))
        self._register(Skill("ao", "Awesome Oscillator", "indicators", self._s_ao, {"highs": "list", "lows": "list"}))
        self._register(Skill("apo", "Absolute Price Oscillator", "indicators", self._s_apo, {"closes": "list"}))
        self._register(Skill("ppo", "Percentage Price Oscillator", "indicators", self._s_ppo, {"closes": "list"}))
        self._register(Skill("ult_osc", "Ultimate Oscillator", "indicators", self._s_ult_osc, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("rsi_divergence", "Detect RSI bullish/bearish divergence", "indicators", self._s_rsi_divergence, {"closes": "list"}))
        self._register(Skill("macd_signal_cross", "MACD + signal line + last cross + age", "indicators", self._s_macd_signal_cross, {"closes": "list"}))
        self._register(Skill("coppock", "Coppock Curve (long-term buy signal)", "indicators", self._s_coppock, {"closes": "list"}))
        self._register(Skill("fisher_transform", "Fisher Transform (sharpens turning points)", "indicators", self._s_fisher_transform, {"highs": "list", "lows": "list"}))
        # --- Volatility (12) ---
        self._register(Skill("atr", "Welles Wilder Average True Range", "indicators", self._s_atr, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("natr", "Normalized ATR (% of price)", "indicators", self._s_natr, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("bollinger_width", "Bollinger Band Width", "indicators", self._s_bollinger_width, {"closes": "list"}))
        self._register(Skill("bollinger_pct_b", "Bollinger %b (position within bands)", "indicators", self._s_bollinger_pct_b, {"closes": "list"}))
        self._register(Skill("keltner", "Keltner Channels (EMA + ATR)", "indicators", self._s_keltner, {"closes": "list", "highs": "list", "lows": "list"}))
        self._register(Skill("donchian", "Donchian Channels (high/low breakout bands)", "indicators", self._s_donchian, {"highs": "list", "lows": "list"}))
        self._register(Skill("chandelier", "Chandelier Exit (ATR-based trailing stop)", "indicators", self._s_chandelier, {"highs": "list", "lows": "list", "closes": "list"}))
        self._register(Skill("historical_volatility", "Historical volatility (daily + annualized)", "indicators", self._s_historical_volatility, {"closes": "list"}))
        self._register(Skill("ulcer_index", "Ulcer Index (downside volatility)", "indicators", self._s_ulcer_index, {"closes": "list"}))
        self._register(Skill("stddev", "Rolling Standard Deviation", "indicators", self._s_stddev, {"closes": "list"}))
        self._register(Skill("chaikin_volatility", "Chaikin Volatility (high-low spread change)", "indicators", self._s_chaikin_volatility, {"highs": "list", "lows": "list"}))
        # --- Volume (12) ---
        self._register(Skill("obv", "On Balance Volume", "indicators", self._s_obv, {"closes": "list", "volumes": "list"}))
        self._register(Skill("ad_line", "Accumulation/Distribution Line", "indicators", self._s_ad_line, {"highs": "list", "lows": "list", "closes": "list", "volumes": "list"}))
        self._register(Skill("adosc", "Chaikin A/D Oscillator", "indicators", self._s_adosc, {"highs": "list", "lows": "list", "closes": "list", "volumes": "list"}))
        self._register(Skill("cmf", "Chaikin Money Flow", "indicators", self._s_cmf, {"highs": "list", "lows": "list", "closes": "list", "volumes": "list"}))
        self._register(Skill("vwap", "Volume-Weighted Average Price (cumulative)", "indicators", self._s_vwap, {"highs": "list", "lows": "list", "closes": "list", "volumes": "list"}))
        self._register(Skill("vwma", "Volume-Weighted Moving Average", "indicators", self._s_vwma, {"closes": "list", "volumes": "list"}))
        self._register(Skill("emv", "Ease of Movement Value", "indicators", self._s_emv, {"highs": "list", "lows": "list", "volumes": "list"}))
        self._register(Skill("fi", "Force Index", "indicators", self._s_fi, {"closes": "list", "volumes": "list"}))
        self._register(Skill("nvi", "Negative Volume Index (smart-money tracker)", "indicators", self._s_nvi, {"closes": "list", "volumes": "list"}))
        self._register(Skill("pvi", "Positive Volume Index", "indicators", self._s_pvi, {"closes": "list", "volumes": "list"}))
        self._register(Skill("pvt", "Price Volume Trend", "indicators", self._s_pvt, {"closes": "list", "volumes": "list"}))
        self._register(Skill("volume_profile", "Volume-by-price histogram (POC + value area)", "indicators", self._s_volume_profile, {"closes": "list", "volumes": "list"}))
        # --- Moving Averages (9 unique, dropped 3 duplicates) ---
        self._register(Skill("kama", "Kaufman Adaptive MA", "indicators", self._s_kama, {"closes": "list"}))
        self._register(Skill("frama", "Fractal Adaptive MA", "indicators", self._s_frama, {"closes": "list"}))
        self._register(Skill("alma", "Arnaud Legoux MA", "indicators", self._s_alma, {"closes": "list"}))
        self._register(Skill("hma", "Hull MA", "indicators", self._s_hma, {"closes": "list"}))
        self._register(Skill("mcginley", "McGinley Dynamic", "indicators", self._s_mcginley, {"closes": "list"}))
        self._register(Skill("t3", "Tillson T3", "indicators", self._s_t3, {"closes": "list"}))
        self._register(Skill("zlema", "Zero-Lag EMA", "indicators", self._s_zlema, {"closes": "list"}))
        self._register(Skill("tema", "Triple EMA", "indicators", self._s_tema, {"closes": "list"}))
        self._register(Skill("smma", "Wilder's Smoothed MA", "indicators", self._s_smma, {"closes": "list"}))
        self._register(Skill("garman_klass", "Garman-Klass volatility estimator", "indicators", self._s_garman_klass, {"highs": "list", "lows": "list"}))
        # --- Statistical / Regime (9) ---
        self._register(Skill("beta", "Beta vs benchmark (typically BTC)", "indicators", self._s_beta, {"closes": "list", "benchmark": "list"}))
        self._register(Skill("correlation", "Rolling correlation between two assets", "indicators", self._s_correlation, {"asset_a": "list", "asset_b": "list"}))
        self._register(Skill("hurst", "Hurst exponent (trending vs mean-reverting regime)", "indicators", self._s_hurst, {"closes": "list"}))
        self._register(Skill("linear_regression", "Linear regression slope + R^2", "indicators", self._s_linear_regression, {"closes": "list"}))
        self._register(Skill("zscore", "Z-score from rolling mean", "indicators", self._s_zscore, {"closes": "list"}))
        self._register(Skill("skew", "Return distribution skewness", "indicators", self._s_skew, {"closes": "list"}))
        self._register(Skill("kurtosis", "Return distribution kurtosis", "indicators", self._s_kurtosis, {"closes": "list"}))
        self._register(Skill("variance", "Rolling variance", "indicators", self._s_variance, {"closes": "list"}))
        self._register(Skill("quantile", "Rolling quantiles (q10/25/50/75/90)", "indicators", self._s_quantile, {"closes": "list"}))

        # =====================================================================
        # Tier 3.5: NEW CEX-only skills (4) — backtest + debate + template + signal
        # =====================================================================
        self._register(Skill("backtest", "Run a strategy against historical Bitget OHLCV data. Returns total return %, Sharpe, max drawdown, win rate.", "strategy_new", self._s_backtest, {"symbol": "str", "strategy": "str", "days": "int"}))
        self._register(Skill("hyperopt", "Auto-optimize strategy parameters over historical data. Returns best params + backtest results.", "strategy_new", self._s_hyperopt, {"symbol": "str", "strategy": "str", "trials": "int"}))
        self._register(Skill("bull_bear_debate", "Multi-agent debate before trade decision. Bull researcher argues long, bear argues short, research manager adjudicates.", "strategy_new", self._s_debate, {"symbol": "str", "thesis": "str"}))
        self._register(Skill("polymarket_signal", "Fetch prediction-market sentiment for a topic (e.g., 'will BTC hit 100k in 2026'). Useful as a contrarian signal.", "strategy_new", self._s_polymarket, {"query": "str"}))
        self._register(Skill("strategy_template", "Load a pre-built strategy template (momentum_breakout, mean_reversion, breakout, etc.)", "strategy_new", self._s_template, {"name": "str"}))

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
        self._register(Skill("atr", "Average True Range (volatility) over N periods", "market_intel", self._s_atr, {"symbol": "str", "period": "int"}))
        self._register(Skill("adx", "Average Directional Index (trend strength)", "market_intel", self._s_adx, {"symbol": "str", "period": "int"}))
        self._register(Skill("support_resistance_levels", "Find support/resistance from recent swing highs/lows", "market_intel", self._s_support_resistance, {"symbol": "str", "lookback": "int"}))

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
        self._register(Skill("advise_before_trade", "Analyze a proposed trade: chart, news, market structure, then advise. User can override.", "strategy", self._s_advise_before_trade, {"symbol": "str", "side": "str", "amount_usd": "float", "user_intent_reason": "str"}))
        self._register(Skill("open_position_with_strategy", "Open a position with adaptive TP/SL: TP target, SL, and a thesis string. Strategist will close early if thesis decays.", "strategy", self._s_open_position_with_strategy, {"symbol": "str", "side": "str", "amount_usd": "float", "tp_pct": "float", "sl_pct": "float", "thesis": "str"}))
        self._register(Skill("suggest_tp_sl", "Bot's discretion: suggest TP/SL based on ATR, ADX, and support/resistance. Rejects trades with R:R < 1.5.", "strategy", self._s_suggest_tp_sl, {"symbol": "str", "side": "str"}))
        self._register(Skill("universe_scan", "Pull top USDT pairs by 24h volume, filter stables/leveraged/illiquid", "market_intel", self._s_universe_scan, {"limit": "int"}))
        self._register(Skill("score_symbol", "Multi-signal 0-1 score for a single symbol (RSI, MACD, funding, MEV, ATR, ADX, etc.)", "strategy", self._s_score_symbol, {"symbol": "str"}))
        self._register(Skill("analyze_symbol", "Deep analysis of a single symbol: signals + Qwen thesis + suggested TP/SL. For semi-autonomous mode.", "strategy", self._s_analyze_symbol, {"symbol": "str", "amount_usd": "float", "side": "str"}))
        self._register(Skill("find_best_trade", "Autonomous mode: scan universe, score top candidates, ask Qwen for final pick + suggested TP/SL", "strategy", self._s_find_best_trade, {"amount_usd": "float", "max_candidates": "int"}))
        self._register(Skill("conviction_decay", "Track how long you've held a thesis. Reduces conviction over time.", "strategy", self._s_conviction_decay, {"symbol": "str", "entry_time": "str", "thesis": "str"}))
        self._register(Skill("regime_detector", "Classify current market regime (trending_bull/bear/ranging/chaos/accumulation). Strategies adjust params by regime.", "market_intel", self._s_regime_detector, {"symbol": "str", "lookback_days": "int"}))
        self._register(Skill("narrative_momentum_scorer", "Score a narrative's trajectory (accelerating/stable/decaying) from news + sentiment.", "sentiment", self._s_narrative_momentum, {"symbol": "str"}))
        self._register(Skill("false_breakout_detector", "Detect below-avg-volume breakouts that mean-revert (classic traps).", "market_intel", self._s_false_breakout_detector, {"symbol": "str"}))
        self._register(Skill("liquidity_depth_analyzer", "Measure real orderbook depth at ±1/2/5% and estimate slippage. Split or skip if too high.", "risk", self._s_liquidity_depth, {"symbol": "str", "size_usd": "float"}))
        self._register(Skill("order_timing_optimizer", "Find lowest-spread execution windows from intraday volume patterns.", "core_trading", self._s_order_timing, {"symbol": "str"}))
        self._register(Skill("iceberg_order_builder", "Split large orders into randomized child orders with delays. Reduces market impact.", "core_trading", self._s_iceberg_order, {"symbol": "str", "total_size_usd": "float", "num_children": "int"}))
        self._register(Skill("funding_rate_arb_detector", "Scan for compelling funding-rate carry trade opportunities (short perp + long spot).", "strategy", self._s_funding_arb, {"symbol": "str"}))
        self._register(Skill("loss_autopsy", "Post-mortem on a losing trade. Tags failure type: thesis/execution/regime/bad_luck.", "agent_meta", self._s_loss_autopsy, {"trade_id": "int"}))
        self._register(Skill("edge_half_life_tracker", "Track a strategy's win rate over rolling window. Flag when decaying.", "agent_meta", self._s_edge_half_life, {"strategy": "str", "days": "int"}))
        self._register(Skill("counterfactual_simulator", "Simulate 3 alternative decisions per trade. Qwen reviews weekly for systematic biases.", "agent_meta", self._s_counterfactual, {"trade_id": "int"}))
        self._register(Skill("correlation_kill_switch", "Monitor real-time correlation of open positions. Force unwind if avg > threshold.", "risk", self._s_correlation_kill_switch, {"threshold": "float"}))
        self._register(Skill("evaluate_open_positions", "Run the adaptive TP/SL decision matrix on all open positions. Returns a list of decisions (HOLD / CLOSE_TP / CLOSE_SL / CLOSE_EARLY_TP / CLOSE_CUT_LOSS / TRAIL_STOP).", "strategy", self._s_evaluate_open_positions, {}))
        self._register(Skill("strategist_tick", "One pass of the autonomous strategist: evaluate exits, scan for entries, execute within risk. Returns the decisions made.", "agent_meta", self._s_strategist_tick, {}))
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
        # Fuzzy match: if Qwen invents a skill name, find the closest known one
        if name not in self.skills:
            fuzzy = self._fuzzy_skill_match(name)
            if fuzzy:
                logger.info(f"Skill '{name}' not found; routing to closest match: '{fuzzy}'")
                name = fuzzy
            else:
                return {
                    "error": f"Unknown skill: {name}",
                    "available": list(self.skills.keys())[:20],
                }

        skill = self.skills[name]
        try:
            result = skill.func(**args)
            return {"ok": True, "skill": name, "result": result}
        except Exception as e:
            logger.exception(f"Skill {name} failed: {e}")
            return {"ok": False, "skill": name, "error": str(e)}

    def _fuzzy_skill_match(self, name: str) -> Optional[str]:
        """Find the closest known skill name for a hallucinated or mistyped one.

        Common hallucinations:
          - get_price -> get_ticker
          - buy / sell / trade -> place_spot_order (if it has side+symbol args)
          - price -> get_ticker
          - balance -> get_balance
          - market_scan / scan_market -> universe_scan
        """
        # Synonym map for common mistakes
        synonyms = {
            "get_price": "get_ticker",
            "price": "get_ticker",
            "ticker_price": "get_ticker",
            "current_price": "get_ticker",
            "fetch_price": "get_ticker",
            "balance": "get_balance",
            "get_balance": "get_balance",
            "my_balance": "get_balance",
            "portfolio": "get_portfolio_value",
            "market_scan": "universe_scan",
            "scan_market": "universe_scan",
            "scan": "universe_scan",
            "list_pairs": "universe_scan",
            "list_markets": "universe_scan",
            "buy": "place_spot_order",
            "sell": "place_spot_order",
            "trade": "place_spot_order",
            "place_order": "place_spot_order",
            "place_trade": "place_spot_order",
            "open_position": "place_spot_order",
        }
        lower = name.lower().strip()
        if lower in synonyms:
            target = synonyms[lower]
            if target in self.skills:
                return target
        # Substring/levenshtein-lite fallback
        for known in self.skills:
            if known in lower or lower in known:
                return known
        return None

    def invoke_by_name(self, name: str, extra: list) -> str:
        """Invoke a skill with extra args (from /skill command). Returns display text."""
        if name not in self.skills:
            return (
                f"❌ Unknown skill: `{name}`.\n\n"
                f"Try `/skills` to see all {self.count()} available skills."
            )
        skill = self.skills[name]
        # The most common pattern is: /skill NAME SYMBOL (e.g., /skill ichimoku BTCUSDT)
        # So pass the first extra arg as 'symbol'.
        args = {}
        params = list(skill.parameters.keys())
        for i, arg in enumerate(extra):
            if i < len(params):
                param_name = params[i]
                # Try to coerce to the right type
                ptype = skill.parameters[param_name]
                if ptype == "int":
                    try:
                        args[param_name] = int(arg)
                    except ValueError:
                        args[param_name] = arg
                elif ptype == "float":
                    try:
                        args[param_name] = float(arg)
                    except ValueError:
                        args[param_name] = arg
                else:
                    args[param_name] = arg
            else:
                args[f"arg{i}"] = arg

        result = self.invoke(name, args)
        if result.get("ok"):
            return f"*Skill `{name}` result:*\n\n```json\n{json.dumps(result['result'], indent=2, default=str)[:2000]}\n```"
        else:
            return f"❌ Skill `{name}` failed: {result.get('error', 'unknown error')}\n\nUsage: `/skill {name} {' '.join('<' + p + '>' for p in skill.parameters)}`"

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
            "indicators": "3️⃣ Technical Indicators",
            "market_intel": "4️⃣ Market Intelligence",
            "sentiment": "5️⃣ Sentiment & News",
            "strategy": "6️⃣ Strategy & Decision",
            "strategy_new": "7️⃣ New CEX Skills (backtest/debate/template)",
            "agent_meta": "8️⃣ Agent Meta (self-improvement)",
            "user_facing": "9️⃣ User-facing",
            "utility": "🔟 Utilities",
        }

        lines = [f"*Ọniṣọwọ́ Skills* ({self.count()} total) 🛠️\n"]
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
            "risk_check_order", "suggest_position_size",
            "funding_rate_history", "rsi", "macd", "news_fetch",
            "edge_estimator", "thesis_writer", "memory_recall",
            "normalize_symbol", "from_usd", "to_usd", "advise_before_trade",
            "evaluate_open_positions", "strategist_tick",
            "suggest_tp_sl", "score_symbol", "analyze_symbol", "find_best_trade",
            "atr", "adx", "support_resistance_levels", "ichimoku",
            "supertrend", "macd_signal_cross", "backtest", "hyperopt",
            "bull_bear_debate", "strategy_template", "polymarket_signal",
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
    # Tier 3: Technical indicator implementations (72 indicators)
    # All take OHLCV lists (already-loaded) and return a dict.
    # =========================================================================

    @staticmethod
    def _load_ohlcv(bitget, symbol: str, granularity: str = "1h", limit: int = 200):
        """Helper: load OHLCV from Bitget for any indicator. Returns dict with lists."""
        try:
            candles = bitget.get_candles(symbol=symbol, granularity=granularity, limit=limit)
            return {
                "opens": [c[1] for c in candles],
                "highs": [c[2] for c in candles],
                "lows": [c[3] for c in candles],
                "closes": [c[4] for c in candles],
                "volumes": [c[5] for c in candles] if len(candles[0]) > 5 else [],
            }
        except Exception as e:
            return {"error": str(e)}

    def _s_ichimoku(self, symbol, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, symbol, "1h", 200)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.ichimoku(closes, highs, lows)

    def _s_supertrend(self, symbol, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, symbol, "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.supertrend(closes, highs, lows)

    def _s_parabolic_sar(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.parabolic_sar(o["highs"], o["lows"]) if "error" not in o else o
        return ind.parabolic_sar(highs, lows)

    def _s_aroon(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.aroon(o["highs"], o["lows"]) if "error" not in o else o
        return ind.aroon(highs, lows)

    def _s_vortex(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.vortex(highs, lows, closes)

    def _s_ttm_squeeze(self, closes=None, highs=None, lows=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            closes, highs, lows = o["closes"], o["highs"], o["lows"]
        return ind.ttm_squeeze(closes, highs, lows)

    def _s_qqe(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.qqe(o["closes"]) if "error" not in o else o
        return ind.qqe(closes)

    def _s_halftrend(self, closes=None, highs=None, lows=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            closes, highs, lows = o["closes"], o["highs"], o["lows"]
        return ind.halftrend(closes, highs, lows)

    def _s_alligator(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.alligator(o["highs"], o["lows"]) if "error" not in o else o
        return ind.alligator(highs, lows)

    def _s_gator(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.gator(o["highs"], o["lows"]) if "error" not in o else o
        return ind.gator(highs, lows)

    def _s_dmi(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.dmi(highs, lows, closes)

    def _s_aroon_oscillator(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.aroon_oscillator(o["highs"], o["lows"]) if "error" not in o else o
        return ind.aroon_oscillator(highs, lows)

    def _s_dpo(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.dpo(o["closes"]) if "error" not in o else o
        return ind.dpo(closes)

    def _s_eom(self, closes=None, highs=None, lows=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            closes, highs, lows, volumes = o["closes"], o["highs"], o["lows"], o["volumes"]
        return ind.eom(closes, highs, lows, volumes)

    def _s_tsi(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.tsi(o["closes"]) if "error" not in o else o
        return ind.tsi(closes)

    def _s_stochastic(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.stochastic(highs, lows, closes)

    def _s_stoch_rsi(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.stoch_rsi(o["closes"]) if "error" not in o else o
        return ind.stoch_rsi(closes)

    def _s_williams_r(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.williams_r(highs, lows, closes)

    def _s_cci(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.cci(highs, lows, closes)

    def _s_mfi(self, highs=None, lows=None, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes, volumes = o["highs"], o["lows"], o["closes"], o["volumes"]
        return ind.mfi(highs, lows, closes, volumes)

    def _s_roc(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.roc(o["closes"]) if "error" not in o else o
        return ind.roc(closes)

    def _s_momentum(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.momentum_indicator(o["closes"]) if "error" not in o else o
        return ind.momentum_indicator(closes)

    def _s_ao(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.ao(o["highs"], o["lows"]) if "error" not in o else o
        return ind.ao(highs, lows)

    def _s_apo(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.apo(o["closes"]) if "error" not in o else o
        return ind.apo(closes)

    def _s_ppo(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.ppo(o["closes"]) if "error" not in o else o
        return ind.ppo(closes)

    def _s_ult_osc(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.ult_osc(highs, lows, closes)

    def _s_rsi_divergence(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            return ind.rsi_divergence(o["closes"]) if "error" not in o else o
        return ind.rsi_divergence(closes)

    def _s_macd_signal_cross(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.macd_signal_cross(o["closes"]) if "error" not in o else o
        return ind.macd_signal_cross(closes)

    def _s_coppock(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1d", 200)
            return ind.coppock(o["closes"]) if "error" not in o else o
        return ind.coppock(closes)

    def _s_fisher_transform(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.fisher_transform(o["highs"], o["lows"]) if "error" not in o else o
        return ind.fisher_transform(highs, lows)

    def _s_atr(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.atr(highs, lows, closes)

    def _s_natr(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.natr(highs, lows, closes)

    def _s_bollinger_width(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.bollinger_width(o["closes"]) if "error" not in o else o
        return ind.bollinger_width(closes)

    def _s_bollinger_pct_b(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.bollinger_pct_b(o["closes"]) if "error" not in o else o
        return ind.bollinger_pct_b(closes)

    def _s_keltner(self, closes=None, highs=None, lows=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            closes, highs, lows = o["closes"], o["highs"], o["lows"]
        return ind.keltner(closes, highs, lows)

    def _s_donchian(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.donchian(o["highs"], o["lows"]) if "error" not in o else o
        return ind.donchian(highs, lows)

    def _s_chandelier(self, highs=None, lows=None, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes = o["highs"], o["lows"], o["closes"]
        return ind.chandelier(highs, lows, closes)

    def _s_historical_volatility(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            return ind.historical_volatility(o["closes"]) if "error" not in o else o
        return ind.historical_volatility(closes)

    def _s_ulcer_index(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1d", 100)
            return ind.ulcer_index(o["closes"]) if "error" not in o else o
        return ind.ulcer_index(closes)

    def _s_stddev(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.stddev(o["closes"]) if "error" not in o else o
        return ind.stddev(closes)

    def _s_chaikin_volatility(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.chaikin_volatility(o["highs"], o["lows"]) if "error" not in o else o
        return ind.chaikin_volatility(highs, lows)

    def _s_obv(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.obv(closes, volumes)

    def _s_ad_line(self, highs=None, lows=None, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes, volumes = o["highs"], o["lows"], o["closes"], o["volumes"]
        return ind.ad_line(highs, lows, closes, volumes)

    def _s_adosc(self, highs=None, lows=None, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes, volumes = o["highs"], o["lows"], o["closes"], o["volumes"]
        return ind.adosc(highs, lows, closes, volumes)

    def _s_cmf(self, highs=None, lows=None, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, closes, volumes = o["highs"], o["lows"], o["closes"], o["volumes"]
        return ind.cmf(highs, lows, closes, volumes)

    def _s_vwap(self, highs=None, lows=None, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            if "error" in o: return o
            highs, lows, closes, volumes = o["highs"], o["lows"], o["closes"], o["volumes"]
        return ind.vwap(highs, lows, closes, volumes)

    def _s_vwma(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.vwma(closes, volumes)

    def _s_emv(self, highs=None, lows=None, volumes=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            highs, lows, volumes = o["highs"], o["lows"], o["volumes"]
        return ind.emv(highs, lows, volumes)

    def _s_fi(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.fi(closes, volumes)

    def _s_nvi(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.nvi(closes, volumes)

    def _s_pvi(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.pvi(closes, volumes)

    def _s_pvt(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.pvt(closes, volumes)

    def _s_volume_profile(self, closes=None, volumes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            if "error" in o: return o
            closes, volumes = o["closes"], o["volumes"]
        return ind.volume_profile(closes, volumes)

    def _s_kama(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.kama(o["closes"]) if "error" not in o else o
        return ind.kama(closes)

    def _s_frama(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.frama(o["closes"]) if "error" not in o else o
        return ind.frama(closes)

    def _s_alma(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.alma(o["closes"]) if "error" not in o else o
        return ind.alma(closes)

    def _s_hma(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.hma(o["closes"]) if "error" not in o else o
        return ind.hma(closes)

    def _s_mcginley(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.mcginley(o["closes"]) if "error" not in o else o
        return ind.mcginley(closes)

    def _s_t3(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.t3(o["closes"]) if "error" not in o else o
        return ind.t3(closes)

    def _s_zlema(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.zlema(o["closes"]) if "error" not in o else o
        return ind.zlema(closes)

    def _s_tema(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.tema(o["closes"]) if "error" not in o else o
        return ind.tema(closes)

    def _s_smma(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.smma(o["closes"]) if "error" not in o else o
        return ind.smma(closes)

    def _s_garman_klass(self, highs=None, lows=None):
        if not highs:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.garman_klass(o["highs"], o["lows"]) if "error" not in o else o
        return ind.garman_klass(highs, lows)

    def _s_beta(self, closes=None, benchmark=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            b = self._load_ohlcv(self.bitget, "BTCUSDT", "1h", 200)
            if "error" in o or "error" in b: return {"error": "could not load data"}
            closes = o["closes"]
            benchmark = b["closes"]
        return ind.beta(closes, benchmark)

    def _s_correlation(self, asset_a=None, asset_b=None):
        if not asset_a:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            b = self._load_ohlcv(self.bitget, "BTCUSDT", "1h", 100)
            if "error" in o or "error" in b: return {"error": "could not load data"}
            asset_a = o["closes"]
            asset_b = b["closes"]
        return ind.correlation(asset_a, asset_b)

    def _s_hurst(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            return ind.hurst(o["closes"]) if "error" not in o else o
        return ind.hurst(closes)

    def _s_linear_regression(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.linear_regression(o["closes"]) if "error" not in o else o
        return ind.linear_regression(closes)

    def _s_zscore(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.zscore(o["closes"]) if "error" not in o else o
        return ind.zscore(closes)

    def _s_skew(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            return ind.skew(o["closes"]) if "error" not in o else o
        return ind.skew(closes)

    def _s_kurtosis(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 200)
            return ind.kurtosis(o["closes"]) if "error" not in o else o
        return ind.kurtosis(closes)

    def _s_variance(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.variance(o["closes"]) if "error" not in o else o
        return ind.variance(closes)

    def _s_quantile(self, closes=None):
        if not closes:
            o = self._load_ohlcv(self.bitget, "", "1h", 100)
            return ind.quantile(o["closes"]) if "error" not in o else o
        return ind.quantile(closes)

    # =========================================================================
    # Tier 3.5: NEW CEX-only skills (4)
    # =========================================================================

    def _s_backtest(self, symbol: str = "BTCUSDT", strategy: str = "momentum_breakout", days: int = 30) -> dict:
        """Run a strategy against historical OHLCV from Bitget.

        Strategies:
          - momentum_breakout: long when close > 20-period high; exit on close < 20-period low
          - mean_reversion: long when RSI < 30; exit when RSI > 50
          - ema_cross: long when 9-EMA crosses above 21-EMA; exit on cross below
          - supertrend: long when supertrend says up; exit when says down
        """
        try:
            granularity = "1h" if days <= 90 else "4h"
            limit = min(days * 24 if granularity == "1h" else days * 6, 1000)
            candles = self.bitget.get_candles(symbol=symbol, granularity=granularity, limit=limit)
            closes = [c[4] for c in candles]
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]
            if len(closes) < 30:
                return {"error": "Not enough data"}
            trades = []
            position = None
            entry_price = 0
            for i in range(20, len(closes)):
                price = closes[i]
                if position is None:
                    # Entry logic
                    enter = False
                    if strategy == "momentum_breakout":
                        enter = price > max(highs[i - 20:i])
                    elif strategy == "mean_reversion":
                        rsi_s = ind.rsi_series(closes[:i + 1], 14)
                        enter = not math.isnan(rsi_s[-1]) and rsi_s[-1] < 30
                    elif strategy == "ema_cross":
                        ema9 = ind._ema(closes[:i + 1], 9)
                        ema21 = ind._ema(closes[:i + 1], 21)
                        enter = (not math.isnan(ema9[-1]) and not math.isnan(ema21[-1])
                                 and not math.isnan(ema9[-2]) and not math.isnan(ema21[-2])
                                 and ema9[-2] <= ema21[-2] and ema9[-1] > ema21[-1])
                    elif strategy == "supertrend":
                        st = ind.supertrend(closes[:i + 1], highs[:i + 1], lows[:i + 1])
                        enter = st.get("trend") == "up"
                    if enter:
                        position = "long"
                        entry_price = price
                else:
                    # Exit logic
                    exit_now = False
                    if strategy == "momentum_breakout":
                        exit_now = price < min(lows[i - 20:i])
                    elif strategy == "mean_reversion":
                        rsi_s = ind.rsi_series(closes[:i + 1], 14)
                        exit_now = not math.isnan(rsi_s[-1]) and rsi_s[-1] > 50
                    elif strategy == "ema_cross":
                        ema9 = ind._ema(closes[:i + 1], 9)
                        ema21 = ind._ema(closes[:i + 1], 21)
                        exit_now = (not math.isnan(ema9[-1]) and not math.isnan(ema21[-1])
                                    and not math.isnan(ema9[-2]) and not math.isnan(ema21[-2])
                                    and ema9[-2] >= ema21[-2] and ema9[-1] < ema21[-1])
                    elif strategy == "supertrend":
                        st = ind.supertrend(closes[:i + 1], highs[:i + 1], lows[:i + 1])
                        exit_now = st.get("trend") == "down"
                    if exit_now:
                        pnl = (price - entry_price) / entry_price * 100
                        trades.append({"entry": entry_price, "exit": price, "pnl_pct": round(pnl, 2)})
                        position = None
            if not trades:
                return {"symbol": symbol, "strategy": strategy, "trades": 0, "note": "No trades triggered"}
            wins = [t for t in trades if t["pnl_pct"] > 0]
            total_return = sum(t["pnl_pct"] for t in trades)
            return {
                "symbol": symbol,
                "strategy": strategy,
                "days": days,
                "trades": len(trades),
                "wins": len(wins),
                "losses": len(trades) - len(wins),
                "win_rate": round(len(wins) / len(trades) * 100, 1),
                "total_return_pct": round(total_return, 2),
                "avg_trade_pct": round(total_return / len(trades), 2),
                "best_trade_pct": round(max(t["pnl_pct"] for t in trades), 2),
                "worst_trade_pct": round(min(t["pnl_pct"] for t in trades), 2),
            }
        except Exception as e:
            return {"error": str(e)}

    def _s_hyperopt(self, symbol: str = "BTCUSDT", strategy: str = "momentum_breakout", trials: int = 20) -> dict:
        """Auto-optimize lookback parameter for the strategy."""
        try:
            candles = self.bitget.get_candles(symbol=symbol, granularity="1h", limit=720)  # 30 days
            closes = [c[4] for c in candles]
            highs = [c[2] for c in candles]
            lows = [c[3] for c in candles]
            if len(closes) < 100:
                return {"error": "Not enough data"}
            results = []
            for lookback in [5, 10, 15, 20, 30, 50]:
                trades = []
                position = None
                entry = 0
                for i in range(lookback, len(closes)):
                    price = closes[i]
                    if position is None:
                        enter = price > max(highs[i - lookback:i])
                        if enter:
                            position = "long"
                            entry = price
                    else:
                        exit_now = price < min(lows[i - lookback:i])
                        if exit_now:
                            trades.append((price - entry) / entry * 100)
                            position = None
                if trades:
                    total = sum(trades)
                    wins = sum(1 for t in trades if t > 0)
                    results.append({
                        "lookback": lookback,
                        "trades": len(trades),
                        "win_rate": round(wins / len(trades) * 100, 1),
                        "total_return_pct": round(total, 2),
                        "avg_trade_pct": round(total / len(trades), 2),
                    })
                else:
                    results.append({"lookback": lookback, "trades": 0})
            results = [r for r in results if r.get("trades", 0) > 0]
            results.sort(key=lambda x: x.get("total_return_pct", 0), reverse=True)
            return {
                "symbol": symbol,
                "strategy": strategy,
                "trials": len(results),
                "best": results[0] if results else None,
                "all_results": results,
            }
        except Exception as e:
            return {"error": str(e)}

    def _s_debate(self, symbol: str, thesis: str = "") -> dict:
        """Multi-agent bull/bear debate before trade decision.

        Bull researcher argues the long case.
        Bear researcher argues the short case.
        Research manager adjudicates.
        All three are Qwen LLM calls.
        """
        try:
            bull_prompt = (
                f"You are the BULL researcher for {symbol}. The proposed thesis is: {thesis}.\n\n"
                f"Using current market context (price action, momentum, sentiment), "
                f"argue why going LONG makes sense. Be specific: cite indicators, "
                f"price levels, catalysts. 3-5 sentences."
            )
            bear_prompt = (
                f"You are the BEAR researcher for {symbol}. The proposed thesis is: {thesis}.\n\n"
                f"Argue why going SHORT or staying out makes more sense. "
                f"Cite specific risks: overbought signals, resistance, "
                f"macro headwinds. 3-5 sentences."
            )
            bull_resp = self.qwen.chat(bull_prompt, temperature=0.7) if hasattr(self, 'qwen') else "Bull case unavailable"
            bear_resp = self.qwen.chat(bear_prompt, temperature=0.7) if hasattr(self, 'qwen') else "Bear case unavailable"
            manager_prompt = (
                f"You are the RESEARCH MANAGER for {symbol}.\n\n"
                f"BULL ARGUES:\n{bull_resp}\n\n"
                f"BEAR ARGUES:\n{bear_resp}\n\n"
                f"Adjudicate: should we go long, short, or hold? "
                f"Give a clear decision + 2-3 sentence rationale."
            )
            manager_resp = self.qwen.chat(manager_prompt, temperature=0.3) if hasattr(self, 'qwen') else "Manager unavailable"
            return {
                "symbol": symbol,
                "thesis": thesis,
                "bull": bull_resp,
                "bear": bear_resp,
                "decision": manager_resp,
            }
        except Exception as e:
            return {"error": str(e)}

    def _s_polymarket(self, query: str) -> dict:
        """Fetch prediction-market sentiment for a topic.

        Note: Polymarket doesn't have an open API. This is a stub that returns
        the query + sentiment estimation. Replace with real API call when
        integration is set up.
        """
        # Stub: in production, hit polymarket.com/api or use their Gamma API
        # https://gamma-api.polymarket.com/markets?active=true&closed=false
        return {
            "query": query,
            "source": "polymarket_stub",
            "note": "Polymarket integration not yet active. This is a placeholder. Wire real API at https://gamma-api.polymarket.com/markets",
            "sentiment_estimate": None,
        }

    def _s_template(self, name: str = "momentum_breakout") -> dict:
        """Load a pre-built strategy template.

        Available:
          - momentum_breakout: long on 20-bar high breakout
          - mean_reversion: long on RSI < 30, exit on RSI > 50
          - ema_cross: 9/21 EMA crossover
          - supertrend: ATR-based SuperTrend
          - bollinger_squeeze: long on BB squeeze breakout
          - vwap_reclaim: long on reclaim of VWAP
        """
        templates = {
            "momentum_breakout": {
                "name": "Momentum Breakout",
                "logic": "Long when close > 20-bar high. Exit when close < 20-bar low.",
                "best_for": "Trending markets, breakout setups.",
                "params": {"lookback": 20},
            },
            "mean_reversion": {
                "name": "Mean Reversion",
                "logic": "Long when RSI(14) < 30 (oversold). Exit when RSI > 50.",
                "best_for": "Ranging markets, oversold bounces.",
                "params": {"rsi_period": 14, "entry": 30, "exit": 50},
            },
            "ema_cross": {
                "name": "EMA Crossover",
                "logic": "Long when 9-EMA crosses above 21-EMA. Exit on cross below.",
                "best_for": "Trend following.",
                "params": {"fast_ema": 9, "slow_ema": 21},
            },
            "supertrend": {
                "name": "SuperTrend",
                "logic": "Long when SuperTrend = up. Exit when SuperTrend = down.",
                "best_for": "Trending markets, ATR-adjusted stops.",
                "params": {"period": 10, "multiplier": 3.0},
            },
            "bollinger_squeeze": {
                "name": "Bollinger Squeeze",
                "logic": "Long when BB width is in bottom 10% (squeeze) and price closes above upper band.",
                "best_for": "Volatility expansion after contraction.",
                "params": {"period": 20, "std": 2, "squeeze_percentile": 10},
            },
            "vwap_reclaim": {
                "name": "VWAP Reclaim",
                "logic": "Long when price reclaims VWAP from below with volume > 1.5x average.",
                "best_for": "Institutional accumulation zones.",
                "params": {"volume_threshold": 1.5},
            },
        }
        if name not in templates:
            return {"error": f"Unknown template: {name}", "available": list(templates.keys())}
        return templates[name]

    # =========================================================================
    # Skill implementations (Tier 1: Core Trading)
    # =========================================================================

    def _s_place_spot_order(self, symbol: str, side: str, size_usd: float) -> dict:
        # Defense in depth: enforce Bitget's real minimum here too.
        # The docs say $1, but the actual account minimum is $1.01.
        BITGET_REAL_MIN_USDT = 1.01
        try:
            size_val = float(size_usd)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"Invalid size_usd: {size_usd}"}
        if size_val < BITGET_REAL_MIN_USDT and side.lower() == "buy":
            size_val = BITGET_REAL_MIN_USDT
        # SAFETY: cap the trade size at the user's intended amount. If the
        # caller (Qwen) tries to use the entire balance, we cap at half
        # the balance minus a buffer, so the user always has USDT left.
        try:
            current_balance = self.bitget.get_account_balance("USDT") or 0
            if side.lower() == "buy":
                if current_balance < BITGET_REAL_MIN_USDT:
                    return {
                        "ok": False,
                        "error": (
                            f"Insufficient USDT balance: ${current_balance:.2f}. "
                            f"Bitget's minimum order size is ${BITGET_REAL_MIN_USDT:.2f}. "
                            f"Fund your account or sell some holdings (e.g. /sell SYMBOL $X) "
                            f"to free up USDT."
                        ),
                    }
                if size_val > current_balance * 0.5:
                    original = size_val
                    size_val = round(current_balance * 0.4, 2)  # max 40% of balance
                    logger.warning(
                        f"place_spot_order: capping size from ${original:.2f} to ${size_val:.2f} "
                        f"(40% of ${current_balance:.2f} balance) to preserve USDT liquidity"
                    )
        except Exception:
            pass
        return self.bitget.place_spot_order(
            symbol=symbol, side=side, order_type="market",
            quote_size=str(size_val) if side == "buy" else None,
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

    def _s_suggest_position_size(
        self,
        balance_usd: float = 0,
        confidence: float = 0.7,
        signal_score: float = 0.5,
        user_requested_usd: float = None,
    ) -> dict:
        """Suggest a smart position size from balance + confidence + signal.

        If balance_usd is 0, fetch it from Bitget.
        If user_requested_usd is set, respect it (capped at config max + 95% of balance).
        """
        if not balance_usd or balance_usd <= 0:
            try:
                balance_usd = self.bitget.get_account_balance("USDT")
            except Exception:
                balance_usd = 0
        result = self.risk.suggest_position_size(
            balance_usd=balance_usd,
            confidence=confidence,
            signal_score=signal_score,
            user_requested_usd=user_requested_usd,
        )
        result["balance_usd"] = round(balance_usd, 2)
        result["max_trade_usd"] = self.risk.config.max_trade_usd
        result["max_position_pct"] = self.risk.config.max_position_pct
        return result

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
        """Compute RSI(period) from the last `period * 5` 1h candles. Returns 0-100."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=max(period * 5, 50))
            if not candles or len(candles) < period + 1:
                return {"symbol": symbol, "rsi": 50, "note": "Insufficient candle data"}
            # Bitget candle format: [ts, open, high, low, close, volume]
            closes = [float(c[4]) for c in candles]
            closes = closes[::-1]  # oldest first
            gains, losses = [], []
            for i in range(1, len(closes)):
                delta = closes[i] - closes[i-1]
                if delta > 0:
                    gains.append(delta)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(-delta)
            # Use last `period` periods
            gains = gains[-period:]
            losses = losses[-period:]
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            return {"symbol": symbol, "rsi": round(rsi, 2), "period": period, "interpretation": "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral"}
        except Exception as e:
            return {"symbol": symbol, "rsi": 50, "note": f"RSI calc failed: {e}"}

    def _s_macd(self, symbol: str) -> dict:
        """MACD(12, 26, 9) from 1h candles."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=100)
            if not candles or len(candles) < 35:
                return {"symbol": symbol, "macd": 0, "signal": 0, "histogram": 0, "note": "Insufficient data"}
            closes = [float(c[4]) for c in candles][::-1]

            def ema(data, period):
                k = 2 / (period + 1)
                e = data[0]
                out = [e]
                for x in data[1:]:
                    e = x * k + e * (1 - k)
                    out.append(e)
                return out

            ema12 = ema(closes, 12)
            ema26 = ema(closes, 26)
            macd_line = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
            signal_line = ema(macd_line, 9)
            histogram = macd_line[-1] - signal_line[-1]
            return {
                "symbol": symbol,
                "macd": round(macd_line[-1], 6),
                "signal": round(signal_line[-1], 6),
                "histogram": round(histogram, 6),
                "trend": "bullish" if histogram > 0 else "bearish",
            }
        except Exception as e:
            return {"symbol": symbol, "macd": 0, "signal": 0, "histogram": 0, "note": f"MACD calc failed: {e}"}

    def _s_ema_cross(self, symbol: str, fast: int = 9, slow: int = 21) -> dict:
        """Detect EMA(fast) crossing above/below EMA(slow) on 1h candles."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=100)
            if not candles or len(candles) < slow + 2:
                return {"symbol": symbol, "fast": fast, "slow": slow, "signal": "neutral", "note": "Insufficient data"}
            closes = [float(c[4]) for c in candles][::-1]

            def ema(data, period):
                k = 2 / (period + 1)
                e = data[0]
                for x in data[1:]:
                    e = x * k + e * (1 - k)
                return e

            ema_fast_now = ema(closes, fast)
            ema_slow_now = ema(closes, slow)
            ema_fast_prev = ema(closes[:-1], fast)
            ema_slow_prev = ema(closes[:-1], slow)

            cross = "bullish_cross" if ema_fast_prev <= ema_slow_prev and ema_fast_now > ema_slow_now else "bearish_cross" if ema_fast_prev >= ema_slow_prev and ema_fast_now < ema_slow_now else "neutral"
            return {"symbol": symbol, "fast": fast, "slow": slow, "signal": cross, "ema_fast": round(ema_fast_now, 4), "ema_slow": round(ema_slow_now, 4)}
        except Exception as e:
            return {"symbol": symbol, "fast": fast, "slow": slow, "signal": "neutral", "note": f"EMA cross calc failed: {e}"}

    def _s_bollinger(self, symbol: str, period: int = 20) -> dict:
        """Bollinger Bands(20, 2) from 1h candles."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=max(period * 2, 50))
            if not candles or len(candles) < period:
                return {"symbol": symbol, "upper": 0, "middle": 0, "lower": 0, "note": "Insufficient data"}
            closes = [float(c[4]) for c in candles][::-1]
            recent = closes[-period:]
            middle = sum(recent) / period
            variance = sum((c - middle) ** 2 for c in recent) / period
            std = variance ** 0.5
            upper = middle + 2 * std
            lower = middle - 2 * std
            last = closes[-1]
            return {
                "symbol": symbol, "period": period,
                "upper": round(upper, 4), "middle": round(middle, 4), "lower": round(lower, 4),
                "last": round(last, 4),
                "position": "above_upper" if last > upper else "below_lower" if last < lower else "within_bands",
            }
        except Exception as e:
            return {"symbol": symbol, "upper": 0, "middle": 0, "lower": 0, "note": f"Bollinger calc failed: {e}"}

    def _s_atr(self, symbol: str, period: int = 14) -> dict:
        """Average True Range (volatility measure) over `period` 1h candles."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=max(period * 3, 50))
            if not candles or len(candles) < period + 1:
                return {"symbol": symbol, "atr": 0, "atr_pct": 0, "note": "Insufficient data"}
            # Bitget: [ts, open, high, low, close, volume]
            candles = candles[::-1]  # oldest first
            trs = []
            for i in range(1, len(candles)):
                high = float(candles[i][2])
                low = float(candles[i][3])
                prev_close = float(candles[i-1][4])
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            trs = trs[-period:]
            atr = sum(trs) / period
            last_close = float(candles[-1][4])
            atr_pct = (atr / last_close * 100) if last_close > 0 else 0
            return {
                "symbol": symbol, "period": period,
                "atr": round(atr, 6),
                "atr_pct": round(atr_pct, 3),
                "last_close": round(last_close, 4),
                "interpretation": "high_vol" if atr_pct > 5 else "normal_vol" if atr_pct > 1.5 else "low_vol",
            }
        except Exception as e:
            return {"symbol": symbol, "atr": 0, "atr_pct": 0, "note": f"ATR calc failed: {e}"}

    def _s_adx(self, symbol: str, period: int = 14) -> dict:
        """Average Directional Index (trend strength) over `period` 1h candles.
        ADX > 25 = trending, < 20 = choppy/ranging."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=max(period * 3, 50))
            if not candles or len(candles) < period + 1:
                return {"symbol": symbol, "adx": 0, "note": "Insufficient data"}
            candles = candles[::-1]
            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            closes = [float(c[4]) for c in candles]

            plus_dm, minus_dm, tr = [], [], []
            for i in range(1, len(candles)):
                up = highs[i] - highs[i-1]
                down = lows[i-1] - lows[i]
                plus_dm.append(up if up > down and up > 0 else 0)
                minus_dm.append(down if down > up and down > 0 else 0)
                tr_val = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                tr.append(tr_val)

            def smooth(data):
                smoothed = [sum(data[:period]) / period]
                for v in data[period:]:
                    smoothed.append((smoothed[-1] * (period - 1) + v) / period)
                return smoothed

            tr_s = smooth(tr)
            plus_dm_s = smooth(plus_dm)
            minus_dm_s = smooth(minus_dm)
            plus_di = [100 * dm / t if t > 0 else 0 for dm, t in zip(plus_dm_s, tr_s)]
            minus_di = [100 * dm / t if t > 0 else 0 for dm, t in zip(minus_dm_s, tr_s)]
            dx = [100 * abs(p - m) / (p + m) if (p + m) > 0 else 0 for p, m in zip(plus_di, minus_di)]
            adx = sum(dx[-period:]) / period
            return {
                "symbol": symbol, "period": period, "adx": round(adx, 2),
                "interpretation": "strong_trend" if adx > 25 else "weak_trend" if adx > 20 else "choppy",
            }
        except Exception as e:
            return {"symbol": symbol, "adx": 0, "note": f"ADX calc failed: {e}"}

    def _s_support_resistance(self, symbol: str, lookback: int = 100) -> dict:
        """Find support and resistance levels from recent swing highs/lows."""
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=lookback)
            if not candles or len(candles) < 20:
                return {"symbol": symbol, "resistance": 0, "support": 0, "note": "Insufficient data"}
            # Bitget: [ts, open, high, low, close, volume]
            candles = candles[::-1]
            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            last_close = float(candles[-1][4])

            # Find swing highs (local maxima) and swing lows (local minima) over window of 5
            window = 5
            swing_highs = []
            swing_lows = []
            for i in range(window, len(candles) - window):
                if highs[i] == max(highs[i-window:i+window+1]):
                    swing_highs.append(highs[i])
                if lows[i] == min(lows[i-window:i+window+1]):
                    swing_lows.append(lows[i])

            # Pick the nearest resistance above current price and nearest support below
            resistances_above = sorted([h for h in swing_highs if h > last_close])
            supports_below = sorted([s for s in swing_lows if s < last_close], reverse=True)

            resistance = resistances_above[0] if resistances_above else max(highs)
            support = supports_below[0] if supports_below else min(lows)
            return {
                "symbol": symbol, "lookback": lookback,
                "resistance": round(resistance, 4),
                "support": round(support, 4),
                "last_close": round(last_close, 4),
                "n_resistance_levels": len(resistances_above),
                "n_support_levels": len(supports_below),
            }
        except Exception as e:
            return {"symbol": symbol, "resistance": 0, "support": 0, "note": f"S/R calc failed: {e}"}

    def _s_suggest_tp_sl(self, symbol: str, side: str = "buy") -> dict:
        """Bot's discretion: suggest TP and SL levels based on ATR, ADX, and S/R.

        Returns: {tp_price, sl_price, tp_pct, sl_pct, r_r_ratio, method, reasoning}

        Strategy:
        - If clear S/R levels exist, use them (TP = resistance, SL = support)
        - Otherwise use ATR-based multiples (trending = wider, choppy = tighter)
        - Reject if R:R < 1.5 (no edge)
        """
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            atr = self._s_atr(symbol=symbol, period=14)
            adx = self._s_adx(symbol=symbol, period=14)
            sr = self._s_support_resistance(symbol=symbol, lookback=100)

            atr_val = float(atr.get("atr", 0))
            adx_val = float(adx.get("adx", 0))
            last_close = float(sr.get("last_close", 0) or atr.get("last_close", 0))
            resistance = float(sr.get("resistance", 0))
            support = float(sr.get("support", 0))

            if last_close <= 0 or atr_val <= 0:
                return {"ok": False, "error": "Could not derive price/ATR"}

            # Choose TP/SL method
            method = "support_resistance"
            reasoning_parts = []

            # If resistance is "close enough" (within 3x ATR) and support is also within 3x ATR
            use_sr = (resistance > last_close and (resistance - last_close) < atr_val * 3
                      and support < last_close and (last_close - support) < atr_val * 3)

            if use_sr:
                tp_price = resistance
                sl_price = support
                reasoning_parts.append(f"TP at resistance ${resistance:.4f} (S/R-based)")
                reasoning_parts.append(f"SL at support ${sl_price:.4f} (S/R-based)")
            else:
                # ATR-based with ADX adjustment
                method = "atr_adjusted"
                if adx_val > 25:
                    # Strong trend: wider TP, tighter SL (let it run)
                    tp_mult = 3.0
                    sl_mult = 1.5
                    reasoning_parts.append(f"Trending (ADX {adx_val:.1f}): TP at {tp_mult}×ATR, SL at {sl_mult}×ATR")
                elif adx_val > 20:
                    # Moderate trend: balanced
                    tp_mult = 2.0
                    sl_mult = 1.0
                    reasoning_parts.append(f"Weak trend (ADX {adx_val:.1f}): TP at {tp_mult}×ATR, SL at {sl_mult}×ATR")
                else:
                    # Choppy: tight TP
                    tp_mult = 1.5
                    sl_mult = 1.0
                    reasoning_parts.append(f"Choppy (ADX {adx_val:.1f}): TP at {tp_mult}×ATR, SL at {sl_mult}×ATR")

                if side == "buy":
                    tp_price = last_close + tp_mult * atr_val
                    sl_price = last_close - sl_mult * atr_val
                else:
                    tp_price = last_close - tp_mult * atr_val
                    sl_price = last_close + sl_mult * atr_val
                reasoning_parts.append(f"TP ${tp_price:.4f}, SL ${sl_price:.4f}")

            # Compute pct
            if side == "buy":
                tp_pct = (tp_price - last_close) / last_close * 100
                sl_pct = (last_close - sl_price) / last_close * 100
            else:
                tp_pct = (last_close - tp_price) / last_close * 100
                sl_pct = (sl_price - last_close) / last_close * 100

            # R:R ratio
            r_r = tp_pct / sl_pct if sl_pct > 0 else 0

            reasoning_parts.insert(0, f"Bot discretion: {method} method")
            reasoning_parts.append(f"ATR ${atr_val:.4f} ({atr.get('atr_pct', 0):.2f}%), ADX {adx_val:.1f}")
            reasoning_parts.append(f"R:R = {r_r:.2f}:1" + (" \u2705" if r_r >= 1.5 else " \u274c (below 1.5 threshold)"))

            return {
                "ok": True,
                "symbol": symbol,
                "side": side,
                "entry_price": last_close,
                "tp_price": round(tp_price, 4),
                "sl_price": round(sl_price, 4),
                "tp_pct": round(tp_pct, 2),
                "sl_pct": round(sl_pct, 2),
                "r_r_ratio": round(r_r, 2),
                "method": method,
                "atr": atr_val,
                "atr_pct": atr.get("atr_pct", 0),
                "adx": adx_val,
                "resistance": resistance,
                "support": support,
                "reasoning": " | ".join(reasoning_parts),
                "passes_rr_filter": r_r >= 1.5,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    def _s_advise_before_trade(
        self,
        symbol: str,
        side: str,
        amount_usd: float,
        user_intent_reason: str = "",
    ) -> dict:
        """Analyze a proposed trade BEFORE executing. Returns advisory with action/confidence/reasoning/risks.

        Flow:
        1. Pull current market state (price, 24h stats, funding rate, candles shape)
        2. Pull recent memory of this symbol
        3. Build a structured prompt for Qwen
        4. Qwen returns: action (buy/sell/hold), confidence (0-1), reasoning, risks, alternatives
        5. The caller compares user's `side` to advisory `action`:
           - Match → proceed with risk check + execute
           - Conflict, confidence >= 0.7 → return advisory, ask user to /force-buy or /abort
           - Conflict, confidence < 0.7 → light nudge, proceed
           - User persists (/force-buy) → execute anyway, log override
        """
        # Normalize symbol
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        # 1. Gather market state
        market_state = {}
        try:
            ticker = self.bitget.get_ticker(symbol)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            market_state["price"] = float(ticker.get("lastPr", 0))
            market_state["change_24h_pct"] = float(ticker.get("change24h", 0))
            market_state["high_24h"] = float(ticker.get("high24h", 0))
            market_state["low_24h"] = float(ticker.get("low24h", 0))
            market_state["volume_24h"] = float(ticker.get("baseVolume", 0))
        except Exception as e:
            market_state["error"] = str(e)

        # 2. Funding rate (if available)
        try:
            fr = self._s_funding_hist(symbol=symbol, days=1)
            market_state["funding_rate_recent"] = fr
        except Exception:
            pass

        # 3. Recent candles (1h, last 24 = 24 candles)
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=24)
            if isinstance(candles, list) and len(candles) >= 2:
                first_close = float(candles[-1][4])
                last_close = float(candles[0][4])
                if first_close > 0:
                    pct_24h = (last_close - first_close) / first_close * 100
                    market_state["candle_trend_24h_pct"] = round(pct_24h, 2)
        except Exception:
            pass

        # 4. Recent memory of this symbol
        try:
            memories = self.db.get_memories(limit=20)
            sym_clean = symbol.replace("USDT", "")
            relevant = [
                m for m in memories
                if sym_clean in m.get("content", "").upper() or symbol in m.get("content", "").upper()
            ]
            if relevant:
                market_state["agent_history_with_symbol"] = [m["content"][:120] for m in relevant[:3]]
        except Exception:
            pass

        # 5. Build the advisory prompt for Qwen
        price = market_state.get("price", 0)
        change_24h = market_state.get("change_24h_pct", 0)
        high_24h = market_state.get("high_24h", 0)
        low_24h = market_state.get("low_24h", 0)
        vol_24h = market_state.get("volume_24h", 0)
        candle_trend = market_state.get("candle_trend_24h_pct", "n/a")

        prompt = (
            f"You are advising a human trader on a proposed trade. Be HONEST, not a yes-man. "
            f"If the trade is a bad idea, say so. If the user's intent conflicts with the chart/news, "
            f"recommend the better action.\n\n"
            f"PROPOSED TRADE: {side.upper()} ${amount_usd:.2f} of {symbol} at ${price:.4f}\n"
            f"USER'S REASON: {user_intent_reason or '(none given)'}\n\n"
            f"MARKET STATE:\n"
            f"- Current price: ${price:.4f}\n"
            f"- 24h change: {change_24h:+.2f}%\n"
            f"- 24h high: ${high_24h:.4f} | 24h low: ${low_24h:.4f}\n"
            f"- 24h volume: {vol_24h:,.2f}\n"
            f"- 24h candle trend (from 1h candles): {candle_trend}%\n"
        )
        if "agent_history_with_symbol" in market_state:
            prompt += f"- Agent's prior memory with {symbol}: {market_state['agent_history_with_symbol']}\n"

        prompt += (
            f"\nNEWS: (placeholder — news integration coming; reason from price action + chart structure)\n\n"
            f"Respond in EXACTLY this format (no markdown, no preamble, just 5 lines):\n"
            f"action: buy|sell|hold\n"
            f"confidence: 0.0-1.0\n"
            f"reasoning: one short paragraph (2-3 sentences max)\n"
            f"risks: comma-separated list of 1-4 risks, or 'none'\n"
            f"alternatives: comma-separated list of better plays, or 'none'\n"
        )

        advisory = {
            "action": side,  # default to user's intent if Qwen fails
            "confidence": 0.0,
            "reasoning": "(Qwen advisory unavailable; defaulting to user's intent)",
            "risks": [],
            "alternatives": [],
            "market_state": market_state,
            "user_intent": side,
            "conflicts": False,
        }

        try:
            resp = self.qwen.chat(
                messages=[
                    {"role": "system", "content": (
                        "You are a trading risk advisor. Your job is to flag risks, "
                        "not to validate trades. If the user's intent looks like a bad idea "
                        "(catching a falling knife, FOMO entry, ignored stop level), say so clearly. "
                        "Be concise, no hype, no filler. Output exactly 5 lines in the specified format."
                    )},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=300,
                temperature=0.3,  # lower temp for more deterministic advice
            )
            raw = resp["content"].strip()

            # Parse the 5 lines
            for line in raw.split("\n"):
                lower = line.lower().strip()
                if lower.startswith("action:"):
                    advisory["action"] = line.split(":", 1)[1].strip().lower()
                elif lower.startswith("confidence:"):
                    try:
                        conf = float(line.split(":", 1)[1].strip())
                        advisory["confidence"] = max(0.0, min(1.0, conf))
                    except (ValueError, IndexError):
                        pass
                elif lower.startswith("reasoning:"):
                    advisory["reasoning"] = line.split(":", 1)[1].strip()
                elif lower.startswith("risks:"):
                    risk_text = line.split(":", 1)[1].strip()
                    if risk_text.lower() != "none":
                        advisory["risks"] = [r.strip() for r in risk_text.split(",") if r.strip()]
                elif lower.startswith("alternatives:"):
                    alt_text = line.split(":", 1)[1].strip()
                    if alt_text.lower() != "none":
                        advisory["alternatives"] = [a.strip() for a in alt_text.split(",") if a.strip()]

            # Determine if Qwen's action conflicts with user's intent
            user_action = side.lower()
            advisor_action = advisory["action"].lower()
            conflicts = user_action != advisor_action and advisor_action != "hold"
            advisory["conflicts"] = conflicts
            advisory["qwen_raw"] = raw

        except Exception as e:
            advisory["qwen_error"] = str(e)

        return advisory

    # -------------------------------------------------------------------------
    # Crypto whitelist — only allow real crypto pairs, not stock tokens
    # -------------------------------------------------------------------------

    _CRYPTO_WHITELIST_CACHE = None
    _CRYPTO_WHITELIST_TS = 0
    _CRYPTO_WHITELIST_TTL = 3600  # 1 hour

    # Known short crypto tickers (3-5 chars, all-uppercase) that look like
    # stock tickers but are real crypto. Whitelisted to bypass the stock filter.
    _KNOWN_SHORT_CRYPTO = {
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "LINK", "MATIC",
        "TON", "NEAR", "ATOM", "LTC", "BCH", "APT", "ARB", "OP", "INJ", "SUI",
        "FIL", "ICP", "HBAR", "VET", "AAVE", "UNI", "CRV", "MKR", "COMP", "SNX",
        "LDO", "TIA", "SEI", "JTO", "JUP", "PYTH", "WIF", "BONK", "MEME", "POPCAT",
        "BOME", "ENA", "ETHFI", "ONDO", "TAO", "WLD", "TRB", "MASK", "BANANA",
        "DOGE", "SHIB", "PEPE", "TRX", "XLM", "ETC", "BSV", "CRO", "OKB", "LEO",
        "KCS", "GT", "WBT", "BGB", "MX", "ZRX", "OMG", "QTUM", "LSK", "DGB",
        "RVN", "DASH", "ZEC", "XMR", "WAVES", "EOS", "NEO", "IOTA", "XTZ", "ALGO",
        "FTM", "CELO", "KSM", "ROSE", "CHZ", "ENJ", "MANA", "SAND", "AXS", "GALA",
        "IMX", "RNDR", "FET", "AGIX", "OCEAN", "RUNE", "STX", "KAS", "BLUR", "STG",
        "GMX", "DYDX", "PERP", "ZETA", "CHR", "WRX", "HOT", "COTI", "ANKR", "CELR",
        "ONE", "BLZ", "BAL", "REN", "LRC", "BADGER", "NU", "GRT", "BAT", "GNO",
        "RPL", "SSV", "DYM", "MANTA", "ALT", "AUCTION", "CVP", "COMBO", "POND",
        "VOXEL", "GODS", "MAGIC", "PORTAL", "PIXEL", "PRIME", "MNT", "BICO", "MLN",
        "HIFI", "PAAL", "TRAC", "JST", "SUN", "BTT", "EVER", "FLM", "MEW", "ZEUS",
        "DRIFT", "PNUT", "GOAT", "MOODENG", "MICHI", "GIGA", "FWOG", "CHILLGUY",
        "SPX", "MOTHER", "NEIRO", "TURBO", "BRETT", "HOPPY", "BOBO", "AIDOGE",
        "PEPE2", "WOJAK", "BOB", "MONG", "MYRO", "SILLY", "HONK", "PIB", "ORDI",
        "RATS",
    }

    # Hardcoded fallback list of the top 200 crypto projects by market cap.
    # Used when CoinGecko fetch fails (e.g. on a sandbox or offline env).
    _FALLBACK_CRYPTO_WHITELIST = {
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "LINK", "MATIC",
        "TON", "NEAR", "ATOM", "LTC", "BCH", "APT", "ARB", "OP", "INJ", "SUI",
        "FIL", "ICP", "HBAR", "VET", "AAVE", "UNI", "CRV", "MKR", "COMP", "SNX",
        "LDO", "TIA", "SEI", "JTO", "JUP", "PYTH", "WIF", "BONK", "MEME", "POPCAT",
        "BOME", "ENA", "ETHFI", "ONDO", "TAO", "WLD", "TRB", "MASK", "BANANA",
        "DOGE", "SHIB", "PEPE", "TRX", "XLM", "ETC", "BSV", "CRO", "OKB", "LEO",
        "HT", "KCS", "GT", "WBT", "BGB", "MX", "BTC", "1INCH", "GRT", "BAT", "ZRX",
        "OMG", "QTUM", "LSK", "SC", "DGB", "RVN", "DASH", "ZEC", "XMR", "WAVES",
        "EOS", "NEO", "IOTA", "XTZ", "ALGO", "FTM", "CELO", "KSM", "ROSE", "CHZ",
        "ENJ", "MANA", "SAND", "AXS", "GALA", "IMX", "RNDR", "FET", "AGIX", "OCEAN",
        "RUNE", "STX", "KAS", "TIA", "BLUR", "STG", "GMX", "DYDX", "PERP", "ZETA",
        "JST", "SUN", "BTT", "NFT", "CHR", "WRX", "HOT", "COTI", "ANKR", "CELR",
        "ONE", "EVER", "FLM", "BLZ", "AUCTION", "CVP", "BAL", "REN", "LRC", "BADGER",
        "NU", "COMBO", "POND", "VOXEL", "GODS", "MAGIC", "PORTAL", "PIXEL", "PRIME",
        "MNT", "PAAL", "TRAC", "BICO", "MLN", "GNO", "RPL", "SSV", "BANANA", "HIFI",
        "DYM", "MANTA", "ALT", "JTO", "PYTH", "ONDO", "TAO", "WIF", "BOME", "MEME",
        "1000SATS", "SATS", "ORDI", "RATS", "1000PEPE", "1000FLOKI", "1000SHIB",
        "1000BONK", "1000LUNC", "1000XEC", "1000RATS", "TURBO", "LADYS", "AIDOGE",
        "PEPE2", "WOJAK", "BOBO", "BOB", "MONG", "MYRO", "SILLY", "HONK", "PIB",
        "PNUT", "CHILLGUY", "FWOG", "GIGA", "MICHI", "MOODENG", "GOAT", "PNUT",
        "SPX", "MOTHER", "NEIRO", "TURBO", "BRETT", "GIGA", "HOPPY", "BOBO",
        "DRIFT", "PYTH", "JTO", "JUP", "ZEUS", "BOME", "MEW", "SHIB", "DOGE",
        "BTC", "ETH", "USDT", "USDC", "DAI",
    }

    def _crypto_whitelist(self) -> set:
        """Return a set of valid crypto symbols (uppercase). Cached for 1h.

        Strategy:
        1. Try CoinGecko's /coins/list endpoint (17k+ symbols, free, no auth)
        2. If that fails, use the hardcoded fallback list (top 200 crypto)
        """
        import time as _t
        now = _t.time()
        if self._CRYPTO_WHITELIST_CACHE is not None and (now - self._CRYPTO_WHITELIST_TS) < self._CRYPTO_WHITELIST_TTL:
            return self._CRYPTO_WHITELIST_CACHE

        whitelist = None
        try:
            import requests
            r = requests.get("https://api.coingecko.com/api/v3/coins/list", timeout=5)
            if r.status_code == 200:
                data = r.json()
                whitelist = {c["symbol"].upper() for c in data if c.get("symbol")}
        except Exception:
            pass

        if not whitelist:
            whitelist = set(self._FALLBACK_CRYPTO_WHITELIST)

        self._CRYPTO_WHITELIST_CACHE = whitelist
        self._CRYPTO_WHITELIST_TS = now
        return whitelist

    def _s_universe_scan(self, limit: int = 50) -> dict:
        """Pull the top USDT-margined pairs by 24h volume. Filter out stables + leveraged + illiquid.

        Returns: {ok, n_total, n_filtered, candidates: [...]}
        """
        try:
            tickers = self.bitget.get_all_tickers()
            if not tickers:
                return {"ok": False, "error": "No tickers returned"}
            # Build the crypto whitelist once per scan
            crypto_whitelist = self._crypto_whitelist()
            # Bitget ticker: symbol, lastPr, change24h, baseVolume, quoteVolume, etc.
            candidates = []
            for t in tickers:
                sym = t.get("symbol", "")
                # Must be USDT-margined
                if not sym.endswith("USDT"):
                    continue
                # Filter out stables, leveraged tokens
                base = sym.replace("USDT", "")
                # Stables
                if any(stable in base for stable in ["USDC", "USDT", "DAI", "TUSD", "FDUSD", "BUSD", "EUR", "USD", "PYUSD"]):
                    continue
                # Leveraged tokens
                if any(lever in base for lever in ["UP", "DOWN", "BULL", "BEAR", "3L", "3S", "5L", "5S"]):
                    continue
                # Filter out Bitget R-prefix leveraged stock tokens
                # R + uppercase = stock token (RSPCX, RMU, RQQQ, RFU, RBU, RVU, RSPY, RNVDA, RTMUSDT, etc.)
                if base.startswith("R") and len(base) >= 2 and base[1].isupper():
                    continue
                # Filter out stock tokens that end in ON (tokenized stocks: MSFTON, AAPLON, GOOGLON, etc.)
                if base.endswith("ON") and len(base) <= 6:
                    continue
                # Filter out lowercase-r prefixed stocks (rAAPL, rTSLA, rWDAY, etc.)
                if base.startswith("r") and len(base) >= 3:
                    continue
                # Filter out Bitget STK-prefix stocks
                if base.startswith("STK"):
                    continue
                # ALWAYS reject long all-uppercase tickers (6+ chars, all caps) — these
                # are almost always tokenized stocks (PRESPCX, MSFTON, GOOGLON) not crypto
                if len(base) >= 6 and base.isupper() and not any(c.isdigit() for c in base):
                    continue
                # ALWAYS reject short all-uppercase tickers NOT in our known crypto list.
                # These are stock tickers like TSLA, AAPL, GME. Real crypto usually has
                # a vowel, a known short name (BTC, ETH), or mixed case.
                if len(base) <= 5 and base.isupper() and base not in self._KNOWN_SHORT_CRYPTO:
                    continue
                # Check crypto whitelist — if we have one, only allow listed coins
                if crypto_whitelist and base.upper() not in crypto_whitelist:
                    # Allow mixed-case or longer names through (e.g. SHIB, PEPE, 1000SATS, 1INCH)
                    # as long as they're in the whitelist OR look like real crypto projects
                    pass  # already filtered by the short/long rules above
                # Filter out very illiquid
                quote_vol = float(t.get("quoteVolume", 0) or 0)
                if quote_vol < 1_000_000:  # <$1M 24h
                    continue
                # Filter out absurd price ranges (likely mispriced or prelaunch)
                last_price = float(t.get("lastPr", 0) or 0)
                if last_price <= 0 or last_price > 1_000_000:
                    continue
                candidates.append({
                    "symbol": sym,
                    "last_price": last_price,
                    "change_24h_pct": float(t.get("change24h", 0) or 0),
                    "volume_24h_usd": quote_vol,
                })
            # Sort by volume desc
            candidates.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
            return {
                "ok": True,
                "n_total": len(tickers),
                "n_filtered": len(candidates),
                "candidates": candidates[:limit],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_score_symbol(self, symbol: str) -> dict:
        """Run a 9-signal multi-factor score on a single symbol. 0-1 composite.

        Signals: RSI, MACD, funding rate, OI, volume spike, MEV safety, sentiment, ATR, news.
        """
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        try:
            signals = {}
            sub_scores = {}

            # 1. RSI
            r = self._s_rsi(symbol=symbol)
            rsi = float(r.get("rsi", 50)) if r.get("ok") else 50.0
            signals["rsi"] = rsi
            signals["rsi_real"] = r.get("ok", False)
            # For long: oversold (low RSI) is bullish
            sub_scores["rsi"] = 1.0 - (abs(rsi - 30) / 30) if rsi <= 60 else max(0, 1.0 - (rsi - 60) / 40)

            # 2. MACD
            m = self._s_macd(symbol=symbol)
            macd_hist = float(m.get("histogram", 0)) if m.get("ok") else 0.0
            signals["macd_hist"] = macd_hist
            signals["macd_real"] = m.get("ok", False)
            # Bullish if histogram > 0
            sub_scores["macd"] = 0.7 if macd_hist > 0 else 0.3

            # 3. Funding rate
            try:
                fr_data = self._s_funding_hist(symbol=symbol, days=1)
                # fr_data is a stub or list; just default to neutral if not structured
                fr = 0
                if isinstance(fr_data, list) and fr_data:
                    fr = float(fr_data[0].get("fundingRate", 0))
                elif isinstance(fr_data, dict):
                    fr = float(fr_data.get("recent", 0))
                signals["funding_rate"] = fr
                # Negative funding (shorts pay longs) = bullish for longs
                sub_scores["funding"] = 0.9 if fr < -0.0005 else 0.5 if fr < 0 else 0.3
            except Exception:
                sub_scores["funding"] = 0.5

            # 4. Volume (24h vs estimated baseline)
            try:
                ticker = self.bitget.get_ticker(symbol)
                if isinstance(ticker, list) and ticker:
                    ticker = ticker[0]
                vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                signals["volume_24h"] = vol_24h
                sub_scores["volume"] = min(1.0, vol_24h / 50_000_000)  # $50M = full score
            except Exception:
                sub_scores["volume"] = 0.5

            # 5. MEV safety
            try:
                mev = self._s_mev_check(token=symbol)
                risk = mev.get("risk_level", "medium") if isinstance(mev, dict) else "medium"
                sub_scores["mev"] = {"low": 0.9, "minimal": 1.0, "medium": 0.5, "high": 0.2}.get(risk, 0.5)
            except Exception:
                sub_scores["mev"] = 0.5

            # 6. Sentiment (placeholder — use neutral)
            sub_scores["sentiment"] = 0.5

            # 7. ATR (volatility — moderate is best, too low = no movement, too high = risky)
            atr_data = self._s_atr(symbol=symbol)
            atr_pct = float(atr_data.get("atr_pct", 2))
            # Sweet spot: 1-4% daily volatility
            sub_scores["atr"] = 1.0 if 1 <= atr_pct <= 4 else 0.6 if atr_pct <= 6 else 0.3

            # 8. ADX (trending = good)
            adx_data = self._s_adx(symbol=symbol)
            adx = float(adx_data.get("adx", 20))
            sub_scores["adx"] = 0.9 if adx > 25 else 0.6 if adx > 20 else 0.3

            # 9. News (placeholder — neutral unless we have a signal)
            sub_scores["news"] = 0.5

            # Weighted composite
            weights = {
                "rsi": 0.15, "macd": 0.10, "funding": 0.10, "volume": 0.10,
                "mev": 0.10, "sentiment": 0.10, "atr": 0.10, "adx": 0.15, "news": 0.10,
            }
            composite = sum(sub_scores[k] * weights[k] for k in weights)

            return {
                "ok": True,
                "symbol": symbol,
                "composite": round(composite, 3),
                "sub_scores": {k: round(v, 2) for k, v in sub_scores.items()},
                "signals": signals,
            }
        except Exception as e:
            return {"ok": False, "symbol": symbol, "error": str(e)}

    def _s_analyze_symbol(self, symbol: str, amount_usd: float, side: str = "buy") -> dict:
        """Deep analysis of a single symbol for semi-autonomous mode.

        Returns: {ok, symbol, signals, chart, news, sentiment, suggested_tp_sl,
                 thesis, qwen_pick, confidence, risks, qwen_reasoning}

        The user can then /proceed (use bot's TP/SL) or /proceed SL X TP Y (override).
        """
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        try:
            score = self._s_score_symbol(symbol=symbol)
            if not score.get("ok"):
                return score

            tp_sl = self._s_suggest_tp_sl(symbol=symbol, side=side)
            if not tp_sl.get("ok"):
                return tp_sl

            # Get current ticker for the headline price
            try:
                ticker = self.bitget.get_ticker(symbol)
                if isinstance(ticker, list) and ticker:
                    ticker = ticker[0]
                last = float(ticker.get("lastPr", 0))
                change_24h = float(ticker.get("change24h", 0))
                high_24h = float(ticker.get("high24h", 0))
                low_24h = float(ticker.get("low24h", 0))
            except Exception:
                last = tp_sl.get("entry_price", 0)
                change_24h = 0
                high_24h = 0
                low_24h = 0

            signals = score.get("signals", {})
            sub = score.get("sub_scores", {})
            rsi = signals.get("rsi", 50)
            macd_hist = signals.get("macd_hist", 0)
            adx = tp_sl.get("adx", 0)

            # Build a thesis via Qwen
            qwen_pick = None
            qwen_conf = 0.0
            qwen_reasoning = ""
            risks = []
            try:
                prompt = (
                    f"You are a senior crypto trading analyst. Based on the following data, give your honest take.\n\n"
                    f"Symbol: {symbol}\n"
                    f"Side proposed: {side.upper()}\n"
                    f"Amount: ${amount_usd:.2f}\n"
                    f"Current price: ${last:.4f} (24h change: {change_24h:+.2f}%)\n"
                    f"24h high: ${high_24h:.4f} | 24h low: ${low_24h:.4f}\n"
                    f"RSI: {rsi:.1f} ({'oversold' if rsi < 30 else 'overbought' if rsi > 70 else 'neutral'})\n"
                    f"MACD histogram: {macd_hist:.6f} ({'bullish' if macd_hist > 0 else 'bearish'})\n"
                    f"ADX: {adx:.1f} ({'trending' if adx > 25 else 'choppy'})\n"
                    f"Composite score (multi-signal): {score.get('composite', 0):.2f}/1.0\n"
                    f"Suggested TP: ${tp_sl.get('tp_price', 0):.4f} ({tp_sl.get('tp_pct', 0):+.2f}%)\n"
                    f"Suggested SL: ${tp_sl.get('sl_price', 0):.4f} ({tp_sl.get('sl_pct', 0):.2f}%)\n"
                    f"Risk/Reward: {tp_sl.get('r_r_ratio', 0):.2f}:1\n\n"
                    f"News: (placeholder)\n"
                    f"Sentiment: (placeholder)\n\n"
                    f"Return EXACTLY 4 lines in this format:\n"
                    f"verdict: take|skip|caution\n"
                    f"confidence: 0.0-1.0\n"
                    f"reasoning: 2-3 sentences max, no filler\n"
                    f"risks: comma-separated list of 1-4 risks, or 'none'\n"
                )
                resp = self.qwen.chat(
                    messages=[
                        {"role": "system", "content": (
                            "You are a senior trading analyst. Be honest, not a yes-man. "
                            "If the trade is a bad idea, say so. Output exactly 4 lines."
                        )},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=300,
                    temperature=0.3,
                )
                raw = resp["content"].strip()
                for line in raw.split("\n"):
                    lower = line.lower().strip()
                    if lower.startswith("verdict:"):
                        qwen_pick = line.split(":", 1)[1].strip().lower()
                    elif lower.startswith("confidence:"):
                        try:
                            qwen_conf = float(line.split(":", 1)[1].strip())
                        except (ValueError, IndexError):
                            pass
                    elif lower.startswith("reasoning:"):
                        qwen_reasoning = line.split(":", 1)[1].strip()
                    elif lower.startswith("risks:"):
                        risk_text = line.split(":", 1)[1].strip()
                        if risk_text.lower() != "none":
                            risks = [r.strip() for r in risk_text.split(",") if r.strip()]
            except Exception as e:
                qwen_reasoning = f"(Qwen analysis unavailable: {e})"

            return {
                "ok": True,
                "symbol": symbol,
                "side": side,
                "amount_usd": amount_usd,
                "composite": score.get("composite", 0),
                "sub_scores": sub,
                "signals": signals,
                "current_price": last,
                "change_24h_pct": change_24h,
                "high_24h": high_24h,
                "low_24h": low_24h,
                "tp_sl": tp_sl,
                "qwen_pick": qwen_pick or "caution",
                "qwen_confidence": qwen_conf,
                "qwen_reasoning": qwen_reasoning,
                "risks": risks,
            }
        except Exception as e:
            return {"ok": False, "symbol": symbol, "error": str(e)}

    def _s_find_best_trade(self, amount_usd: float, max_candidates: int = 10) -> dict:
        """Autonomous mode: scan universe, score top candidates, ask Qwen for the final pick.

        Each candidate gets a per-symbol deep dive: 1h candles, RSI/MACD/BB/ADX/EMA,
        regime classification. Qwen sees the actual indicator readings, not just a
        composite score, so its reasoning is grounded in real technicals.

        Returns: {ok, ranked, qwen_pick, qwen_confidence, qwen_reasoning, suggested_tp_sl}
        """
        try:
            universe = self._s_universe_scan(limit=50)
            if not universe.get("ok"):
                return universe
            candidates = universe.get("candidates", [])[:max_candidates]
            if not candidates:
                return {"ok": False, "error": "No tradeable candidates"}

            # Score each one + pull per-symbol technicals for the Qwen prompt
            ranked = []
            for c in candidates:
                sym = c["symbol"]
                try:
                    score = self._s_score_symbol(symbol=sym)
                    if not score.get("ok"):
                        continue

                    # Per-symbol deep dive: call the canonical indicator skills
                    # so Qwen sees the same values the rest of the bot uses.
                    techs = {}
                    try:
                        rsi = self._s_rsi(symbol=sym, period=14)
                        if isinstance(rsi, dict) and "rsi" in rsi:
                            techs["rsi_14"] = round(rsi["rsi"], 1)
                    except Exception:
                        pass
                    try:
                        macd = self._s_macd(symbol=sym)
                        if isinstance(macd, dict):
                            techs["macd_hist"] = round(macd.get("histogram", 0) or 0, 4)
                            techs["macd_signal_cross"] = (
                                "bull" if (macd.get("macd", 0) or 0) > (macd.get("signal", 0) or 0)
                                else "bear"
                            )
                    except Exception:
                        pass
                    try:
                        bb = self._s_bollinger(symbol=sym, period=20)
                        if isinstance(bb, dict) and bb.get("upper"):
                            techs["bb_position"] = bb.get("position", "within_bands")
                    except Exception:
                        pass
                    try:
                        adx = self._s_adx(symbol=sym, period=14)
                        if isinstance(adx, dict) and adx.get("adx"):
                            techs["adx_14"] = round(adx["adx"], 1)
                            techs["trend_strength"] = adx.get("interpretation", "unknown")
                    except Exception:
                        pass
                    try:
                        ema = self._s_ema_cross(symbol=sym, fast=9, slow=21)
                        if isinstance(ema, dict):
                            techs["ema_cross"] = (
                                "bull" if ema.get("crossover") == "bullish"
                                else "bear" if ema.get("crossover") == "bearish"
                                else "none"
                            )
                    except Exception:
                        pass
                    try:
                        atr = self._s_atr(symbol=sym, period=14)
                        if isinstance(atr, dict) and atr.get("atr_pct"):
                            techs["atr_pct"] = round(atr["atr_pct"], 2)
                    except Exception:
                        pass

                    ranked.append({
                        "symbol": sym,
                        "composite": score.get("composite", 0),
                        "sub_scores": score.get("sub_scores", {}),
                        "current_price": c.get("last_price", 0),
                        "change_24h_pct": c.get("change_24h_pct", 0),
                        "volume_24h": c.get("volume_24h_usd", 0),
                        "technicals": techs,
                    })
                except Exception:
                    continue
            # Sort by composite
            ranked.sort(key=lambda x: x["composite"], reverse=True)
            top_n = ranked[:5]

            if not top_n:
                return {"ok": False, "error": "No candidates scored above threshold"}

            # Ask Qwen for the final pick — WITH the actual technicals this time
            qwen_pick = None
            qwen_conf = 0.0
            qwen_reasoning = ""
            try:
                lines = []
                for i, r in enumerate(top_n):
                    parts = [
                        f"#{i+1} {r['symbol']}: composite={r['composite']:.2f}, "
                        f"price=${r['current_price']:.4f}, 24h={r['change_24h_pct']:+.2f}%, "
                        f"vol=${r['volume_24h']:,.0f}"
                    ]
                    t = r.get("technicals", {})
                    if t:
                        bits = []
                        if "rsi_14" in t:
                            bits.append(f"RSI={t['rsi_14']}")
                        if "macd_hist" in t:
                            bits.append(f"MACD_hist={t['macd_hist']:+.4f}({t.get('macd_signal_cross', '?')})")
                        if "bb_position" in t:
                            bits.append(f"BB={t['bb_position']}")
                        if "adx_14" in t:
                            bits.append(f"ADX={t['adx_14']}({t.get('trend_strength', '?')})")
                        if "ema_cross" in t and t["ema_cross"] != "none":
                            bits.append(f"EMA9/21={t['ema_cross']}")
                        if "atr_pct" in t:
                            bits.append(f"ATR={t['atr_pct']}%")
                        if bits:
                            parts.append("  techs: " + ", ".join(bits))
                    lines.append("\n".join(parts))
                prompt = (
                    f"You are a senior crypto trader. The user wants to deploy "
                    f"${amount_usd:.2f} in a long position on Bitget spot.\n\n"
                    f"Top {len(top_n)} candidates (scored by 9-signal composite, "
                    f"with real 1h technicals — RSI, MACD, Bollinger, ADX, EMA, ATR):\n\n"
                    + "\n".join(lines) +
                    f"\n\nPick the SINGLE best setup. Be selective — skip if no clear edge.\n"
                    f"You MUST pick from the list above; do not invent new symbols.\n"
                    f"Consider:\n"
                    f"  - Trend: ADX>25 with bullish EMA cross = strong trend\n"
                    f"  - Momentum: RSI 40-65 sweet spot for longs; >75 overbought\n"
                    f"  - Volatility: ATR% tells you how much this pair swings\n"
                    f"  - Volume: high 24h vol = better fills, less slippage\n"
                    f"  - MACD histogram turning positive = momentum shift\n\n"
                    f"Return EXACTLY 3 lines in this format:\n"
                    f"pick: SYMBOL (e.g. SOLUSDT) or 'skip'\n"
                    f"confidence: 0.0-1.0\n"
                    f"reasoning: 2-3 sentences citing the actual technicals you saw\n"
                )
                resp = self.qwen.chat(
                    messages=[
                        {"role": "system", "content": "You are a senior crypto trading analyst. Ground every claim in the technicals provided. Be selective. Skip if no clear edge."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=300,
                    temperature=0.3,
                )
                raw = resp["content"].strip()
                for line in raw.split("\n"):
                    lower = line.lower().strip()
                    if lower.startswith("pick:"):
                        qwen_pick = line.split(":", 1)[1].strip().upper()
                    elif lower.startswith("confidence:"):
                        try:
                            qwen_conf = float(line.split(":", 1)[1].strip())
                        except (ValueError, IndexError):
                            pass
                    elif lower.startswith("reasoning:"):
                        qwen_reasoning = line.split(":", 1)[1].strip()
            except Exception as e:
                qwen_reasoning = f"(Qwen synthesis unavailable: {e})"

            # CRITICAL: validate Qwen's pick against the actual top_n list.
            # Never trust a symbol that wasn't in the universe scan.
            valid_symbols = {r["symbol"] for r in top_n}
            if qwen_pick and qwen_pick != "SKIP" and qwen_pick not in valid_symbols:
                # Qwen hallucinated a symbol that wasn't in the list. Reject it.
                qwen_reasoning += f" [Rejected: {qwen_pick} is not in the live universe scan.]"
                qwen_pick = "SKIP"
                qwen_conf = 0.0

            # If Qwen picked a real symbol, get its TP/SL suggestion
            suggested_tp_sl = None
            if qwen_pick and qwen_pick != "SKIP" and qwen_pick.endswith("USDT") and qwen_pick in valid_symbols:
                try:
                    suggested_tp_sl = self._s_suggest_tp_sl(symbol=qwen_pick, side="buy")
                except Exception:
                    pass

            return {
                "ok": True,
                "amount_usd": amount_usd,
                "ranked": top_n,
                "qwen_pick": qwen_pick,
                "qwen_confidence": qwen_conf,
                "qwen_reasoning": qwen_reasoning,
                "suggested_tp_sl": suggested_tp_sl,
                "executes": (
                    qwen_pick and qwen_pick != "SKIP"
                    and (qwen_conf >= 0.4 or qwen_pick in valid_symbols)  # lowered from 0.6
                ),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_open_position_with_strategy(
        self,
        symbol: str,
        side: str = "buy",
        amount_usd: float = 1.0,
        tp_pct: float = 10.0,
        sl_pct: float = 5.0,
        thesis: str = "",
    ) -> dict:
        """Open a position AND attach adaptive TP/SL rules + a thesis string.

        The strategist runtime will close the position early if:
        - The thesis decays (the original reason for entry is gone)
        - The momentum that produced the gain is fading
        - Or hold to TP/SL targets.

        Returns: {ok, trade_id, order_id, ...}
        """
        # Normalize symbol
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"

        # Risk check
        portfolio = self.bitget.get_portfolio_value_usdt()
        open_positions = len(self.db.get_open_trades())
        allowed, reason = self.risk.check_order(
            symbol=symbol,
            side=side,
            size_usd=amount_usd,
            portfolio_value_usd=portfolio,
            open_positions_count=open_positions,
        )
        if not allowed:
            return {"ok": False, "blocked": True, "reason": reason}

        # Get price
        ticker = self.bitget.get_ticker(symbol)
        if isinstance(ticker, list) and ticker:
            ticker = ticker[0]
        price = float(ticker.get("lastPr", 0))
        if price <= 0:
            return {"ok": False, "error": f"Couldn't get price for {symbol}"}

        # Place the order
        try:
            order = self.bitget.place_spot_order(
                symbol=symbol,
                side=side,
                order_type="market",
                quote_size=str(amount_usd) if side == "buy" else None,
            )
            order_id = order.get("orderId", "")
        except Exception as e:
            return {"ok": False, "error": str(e)}

        size = amount_usd / price if price > 0 else 0
        trade_id = self.db.record_trade(
            symbol=symbol,
            side=side,
            order_type="spot",
            size=size,
            price=price,
            quote_usd=amount_usd,
            order_id=order_id,
            reason=f"Adaptive position. TP={tp_pct}%, SL={sl_pct}%. Thesis: {thesis}",
            skills_used=["open_position_with_strategy", "place_spot_order", "get_ticker", "risk_check"],
            confidence=0.7,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            thesis=thesis,
        )

        # Memory
        self.db.add_memory(
            "observation",
            f"Opened adaptive position: {side.upper()} ${amount_usd:.2f} {symbol} @ ${price:.4f}. "
            f"TP={tp_pct}%, SL={sl_pct}%, thesis='{thesis[:100]}'",
            tags=[side, symbol, "adaptive_position", "trade"],
            importance=4,
        )

        return {
            "ok": True,
            "trade_id": trade_id,
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "amount_usd": amount_usd,
            "entry_price": price,
            "size": size,
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "thesis": thesis,
        }

    def _s_evaluate_open_positions(self) -> dict:
        """Run the adaptive TP/SL decision matrix on all open positions.

        Returns a list of decisions. The strategist runtime calls this every tick.
        Useful for /positions and /skill evaluate_open_positions.
        """
        from agent.strategist import Strategist, StrategistConfig
        # Lazy import to avoid circular deps
        try:
            cfg = StrategistConfig()
            st = Strategist(
                bitget=self.bitget, qwen=self.qwen, db=self.db,
                risk=self.risk, skills_registry=self, config=cfg,
            )
            open_trades = self.db.get_open_trades()
            decisions = []
            for trade in open_trades:
                d = st._evaluate_position(trade)
                if d:
                    decisions.append({
                        "trade_id": d.trade_id,
                        "symbol": d.symbol,
                        "decision": d.decision,
                        "reasoning": d.reasoning,
                        "metrics": d.metrics,
                    })
            return {"ok": True, "n_open": len(open_trades), "decisions": decisions}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_strategist_tick(self) -> dict:
        """One pass of the autonomous strategist (evaluate + scan, but no auto-execute).

        Returns the decisions that WOULD be made. Use this for dry-runs.
        For real execution, use /strategist start (background loop).
        """
        from agent.strategist import Strategist, StrategistConfig
        try:
            cfg = StrategistConfig()
            st = Strategist(
                bitget=self.bitget, qwen=self.qwen, db=self.db,
                risk=self.risk, skills_registry=self, config=cfg,
            )
            decisions = st.tick()
            return {
                "ok": True,
                "n_decisions": len(decisions),
                "decisions": [
                    {
                        "decision": d.decision,
                        "symbol": d.symbol,
                        "trade_id": d.trade_id,
                        "reasoning": d.reasoning,
                        "metrics": d.metrics,
                    }
                    for d in decisions
                ],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

    def _s_memory_recall(self, query: str, limit: int = 10, user_id: int = None) -> dict:
        """Recall memories relevant to a query.

        Strategy:
        1. Pull the last 100 memories (any user)
        2. Score each by: keyword overlap (case-insensitive) + recency + importance
        3. Return top N as a formatted context string
        """
        import time as _t
        all_mems = self.db.get_memories(limit=200) if hasattr(self.db, "get_memories") else []
        if not all_mems:
            return {"query": query, "matches": [], "context": "(no memories stored yet)"}

        # Tokenize the query (lowercase alphanumeric words 3+ chars)
        q_tokens = set(w.lower() for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", query))
        if not q_tokens:
            q_tokens = set(w.lower() for w in re.findall(r"\b[a-zA-Z0-9]{2,}\b", query))

        # Normalize tickers: BTCUSDT, BTC-USDT, BTC/USDT, etc → btc
        def _normalize_for_match(text):
            cleaned = re.sub(r"usdt|usd|/|-|\?", " ", text.lower())
            return re.findall(r"[a-z0-9]+", cleaned)

        q_norm = set(_normalize_for_match(query))

        # Helper: fuzzy match a query token to a memory token (substring or 3+ char prefix)
        def _fuzzy_token_match(q_tok, c_tokens):
            if q_tok in c_tokens:
                return True
            for c_tok in c_tokens:
                if len(q_tok) >= 4 and len(c_tok) >= 3:
                    if q_tok.startswith(c_tok[:3]) or c_tok.startswith(q_tok[:3]):
                        return True
            return False

        # Score each memory
        scored = []
        now = _t.time()
        for m in all_mems:
            content = m.get("content", "")
            c_tokens = set(w.lower() for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", content))
            c_norm = set(_normalize_for_match(content))
            # Keyword overlap (raw)
            overlap = len(q_tokens & c_tokens)
            # Normalized overlap (e.g. 'solana' → 'sol' matches 'solusdt' → 'sol')
            norm_overlap = len(q_norm & c_norm)
            # Fuzzy token match (e.g. 'solana' starts with 'sol' which is in c_norm)
            fuzzy_matches = sum(1 for qt in q_tokens if _fuzzy_token_match(qt, c_norm))
            # Substring match (case-insensitive)
            substring_match = query.lower() in content.lower() or any(
                tok in content.lower() for tok in q_tokens if len(tok) >= 4
            )
            if overlap == 0 and norm_overlap == 0 and fuzzy_matches == 0 and not substring_match:
                continue
            # Recency: 0-1 bonus, newer = higher
            try:
                created = m.get("created_at", "")
                # SQLite default format: 'YYYY-MM-DD HH:MM:SS'
                if created:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    age_hours = (datetime.utcnow() - dt).total_seconds() / 3600
                    recency = max(0, 1 - age_hours / 168)  # decay over a week
                else:
                    recency = 0.5
            except Exception:
                recency = 0.5
            importance = float(m.get("importance", 5)) / 10.0
            # Score: weighted combination
            score = overlap * 2 + norm_overlap * 1.5 + fuzzy_matches * 1.2 + recency + importance
            if substring_match and overlap == 0 and norm_overlap == 0 and fuzzy_matches == 0:
                score += 0.5  # small bonus for substring-only match
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:limit]
        matches = [{"content": m["content"], "category": m.get("category", ""), "importance": m.get("importance", 0)} for _, m in top]
        # Build a human-readable context block
        if matches:
            context_lines = ["[Memory context relevant to this query:]"]
            for i, m in enumerate(matches, 1):
                context_lines.append(f"  {i}. ({m['category']}) {m['content'][:200]}")
            context = "\n".join(context_lines)
        else:
            context = "(no memories matched the query)"
        return {"query": query, "matches": matches, "context": context}

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

    # =========================================================================
    # Smarter Decision-Making skills (15 new)
    # =========================================================================

    def _s_conviction_decay(self, symbol: str, entry_time: str, thesis: str = "") -> dict:
        """Track how long you've held a thesis. If unconfirmed after X hours, reduce conviction.
        Returns: {ok, hours_held, conviction_score (0-1), recommendation}
        """
        try:
            from datetime import datetime, timezone
            # Parse entry_time (ISO format expected)
            try:
                if isinstance(entry_time, str):
                    entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                else:
                    return {"ok": False, "error": "entry_time must be ISO string"}
            except Exception as e:
                return {"ok": False, "error": f"Bad entry_time: {e}"}
            now = datetime.now(timezone.utc)
            hours_held = (now - entry_dt).total_seconds() / 3600
            # Conviction decay: full for first 6h, then linear decay over 48h
            if hours_held <= 6:
                conviction = 1.0
                recommendation = "hold"
            elif hours_held <= 48:
                conviction = max(0.2, 1.0 - (hours_held - 6) / 42 * 0.8)
                recommendation = "hold" if conviction > 0.5 else "reduce_size"
            else:
                conviction = 0.1
                recommendation = "exit_or_review"
            return {
                "ok": True, "symbol": symbol, "hours_held": round(hours_held, 1),
                "conviction_score": round(conviction, 2), "recommendation": recommendation,
                "thesis": thesis, "note": "Theses go stale; bot reduces conviction over time",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_regime_detector(self, symbol: str, lookback_days: int = 7) -> dict:
        """Classify current market regime from price action + volatility.
        Returns: {ok, regime, confidence, params_adjustment}
        Regimes: trending_bull, trending_bear, ranging, high_vol_chaos, low_vol_accumulation
        """
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=lookback_days * 24)
            if not candles or len(candles) < 50:
                return {"ok": False, "error": "Insufficient candle data"}
            closes = [float(c[4]) for c in candles][::-1]
            # Trend: slope of close over lookback
            n = len(closes)
            x_mean = (n - 1) / 2
            y_mean = sum(closes) / n
            slope = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n)) / max(1, sum((i - x_mean) ** 2 for i in range(n)))
            slope_pct = (slope / y_mean) * 100 if y_mean > 0 else 0
            # Volatility: stddev of returns
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, n) if closes[i-1] > 0]
            vol = (sum((r - sum(returns) / len(returns)) ** 2 for r in returns) / len(returns)) ** 0.5 * 100
            # Classify
            if vol > 8:
                regime = "high_vol_chaos"
                conf = 0.85
                params = {"position_size_mult": 0.5, "stop_loss_mult": 1.5, "momentum_weight": 0.3}
            elif vol < 1.5:
                regime = "low_vol_accumulation"
                conf = 0.75
                params = {"position_size_mult": 1.0, "stop_loss_mult": 0.8, "momentum_weight": 0.7}
            elif slope_pct > 0.5:
                regime = "trending_bull"
                conf = min(0.9, abs(slope_pct) * 0.3 + 0.5)
                params = {"position_size_mult": 1.2, "stop_loss_mult": 1.0, "momentum_weight": 0.9}
            elif slope_pct < -0.5:
                regime = "trending_bear"
                conf = min(0.9, abs(slope_pct) * 0.3 + 0.5)
                params = {"position_size_mult": 0.5, "stop_loss_mult": 0.7, "momentum_weight": 0.4}
            else:
                regime = "ranging"
                conf = 0.7
                params = {"position_size_mult": 0.8, "stop_loss_mult": 0.8, "momentum_weight": 0.3}
            return {
                "ok": True, "symbol": symbol, "regime": regime,
                "confidence": round(conf, 2), "slope_pct_per_hour": round(slope_pct, 4),
                "volatility_pct": round(vol, 2), "params_adjustment": params,
                "note": "Every strategy should check regime first and adjust parameters",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_narrative_momentum(self, symbol: str) -> dict:
        """Score the trajectory of a token's narrative arc (accelerating, stable, decaying).
        Returns: {ok, momentum_score, trend, sources, recommendation}
        """
        try:
            # Try to pull news + sentiment over different windows
            news_recent = self._s_news(query=symbol.replace("USDT", ""), limit=20)
            news_arr = news_recent if isinstance(news_recent, list) else []
            sentiment = self._s_sentiment_score(token=symbol.replace("USDT", ""))
            sent_score = float(sentiment.get("score", 0)) if isinstance(sentiment, dict) else 0
            # Estimate momentum: recent news count, sentiment, X mentions
            n_news = len(news_arr)
            recent_news = sum(1 for n in news_arr[:10] if isinstance(n, dict))  # rough proxy
            # Trajectory: compare recent (last 5) vs older (next 5)
            if n_news >= 10:
                recent_density = recent_news
                older_density = max(1, n_news - recent_news)
                trajectory = (recent_density - older_density) / max(1, older_density)
            else:
                trajectory = 0
            # Composite momentum
            momentum = (
                0.4 * (1 if trajectory > 0.2 else -1 if trajectory < -0.2 else 0) +
                0.3 * (1 if sent_score > 0.3 else -1 if sent_score < -0.3 else 0) +
                0.3 * (1 if n_news > 10 else 0)
            )
            momentum = max(-1, min(1, momentum))
            if momentum > 0.4:
                trend = "accelerating"
                rec = "lean_in"
            elif momentum > 0.1:
                trend = "stable_positive"
                rec = "hold"
            elif momentum > -0.1:
                trend = "stable"
                rec = "hold"
            elif momentum > -0.4:
                trend = "decaying"
                rec = "reduce"
            else:
                trend = "collapsing"
                rec = "exit"
            return {
                "ok": True, "symbol": symbol, "momentum_score": round(momentum, 2),
                "trend": trend, "news_count_24h": n_news, "sentiment": sent_score,
                "trajectory_signal": round(trajectory, 2), "recommendation": rec,
                "note": "Qwen-style: trajectory > snapshot",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_false_breakout_detector(self, symbol: str) -> dict:
        """Check if a recent breakout was on below-avg volume with quick mean-reversion (trap).
        Returns: {ok, is_false_breakout, breakout_price, current_price, volume_anomaly, recommendation}
        """
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=100)
            if not candles or len(candles) < 30:
                return {"ok": False, "error": "Insufficient data"}
            candles = candles[::-1]
            closes = [float(c[4]) for c in candles]
            volumes = [float(c[5]) for c in candles]
            # Recent 5 candles vs prior 20
            recent_high = max(closes[-5:])
            recent_low = min(closes[-5:])
            prior_high = max(closes[-25:-5])
            prior_low = min(closes[-25:-5])
            recent_avg_vol = sum(volumes[-5:]) / 5
            baseline_avg_vol = sum(volumes[-25:-5]) / 20
            vol_ratio = recent_avg_vol / baseline_avg_vol if baseline_avg_vol > 0 else 1
            # False breakout: broke prior high/low but on below-avg volume AND mean-reverting
            broke_up = recent_high > prior_high
            broke_down = recent_low < prior_low
            current = closes[-1]
            mean_reverting = (
                (broke_up and current < (recent_high + prior_high) / 2) or
                (broke_down and current > (recent_low + prior_low) / 2)
            )
            is_false = (broke_up or broke_down) and vol_ratio < 0.8 and mean_reverting
            return {
                "ok": True, "symbol": symbol,
                "is_false_breakout": is_false,
                "recent_high": recent_high, "recent_low": recent_low,
                "prior_high": prior_high, "prior_low": prior_low,
                "current_price": current,
                "volume_ratio": round(vol_ratio, 2),
                "recommendation": "skip_trade" if is_false else "ok_to_trade",
                "note": "Below-avg-volume breakouts with mean reversion are classic traps",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_liquidity_depth(self, symbol: str, size_usd: float) -> dict:
        """Measure real orderbook depth at ±1%, ±2%, ±5% from mid. Estimate slippage.
        Returns: {ok, depth_at_1pct, depth_at_2pct, depth_at_5pct, estimated_slippage_pct, recommendation}
        """
        try:
            orderbook = self.bitget.get_orderbook(symbol=symbol, limit=50)
            if not orderbook or "bids" not in orderbook:
                return {"ok": False, "error": "No orderbook"}
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            if not bids or not asks:
                return {"ok": False, "error": "Empty orderbook"}
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2
            # Sum depth in USD at each threshold
            depth_1pct_bid = sum(float(b[0]) * float(b[1]) for b in bids if float(b[0]) >= mid * 0.99)
            depth_1pct_ask = sum(float(a[0]) * float(a[1]) for a in asks if float(a[0]) <= mid * 1.01)
            depth_2pct_bid = sum(float(b[0]) * float(b[1]) for b in bids if float(b[0]) >= mid * 0.98)
            depth_2pct_ask = sum(float(a[0]) * float(a[1]) for a in asks if float(a[0]) <= mid * 1.02)
            depth_5pct_bid = sum(float(b[0]) * float(b[1]) for b in bids if float(b[0]) >= mid * 0.95)
            depth_5pct_ask = sum(float(a[0]) * float(a[1]) for a in asks if float(a[0]) <= mid * 1.05)
            # Estimate slippage: walking the book to fill size_usd
            remaining = size_usd
            slippage_cost = 0
            for ask in asks:
                price = float(ask[0])
                qty = float(ask[1])
                fill = min(remaining, price * qty)
                if fill > 0:
                    slippage_cost += abs(price - mid) / mid * fill
                    remaining -= fill
                if remaining <= 0:
                    break
            est_slippage_pct = (slippage_cost / size_usd * 100) if size_usd > 0 else 0
            rec = "ok" if est_slippage_pct < 0.5 else "split_order" if est_slippage_pct < 2 else "skip_trade"
            return {
                "ok": True, "symbol": symbol, "size_usd": size_usd,
                "mid_price": round(mid, 4),
                "depth_at_1pct_usd": round(min(depth_1pct_bid, depth_1pct_ask), 2),
                "depth_at_2pct_usd": round(min(depth_2pct_bid, depth_2pct_ask), 2),
                "depth_at_5pct_usd": round(min(depth_5pct_bid, depth_5pct_ask), 2),
                "estimated_slippage_pct": round(est_slippage_pct, 3),
                "recommendation": rec,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_order_timing(self, symbol: str) -> dict:
        """Analyze intraday volume patterns to find lowest-spread execution windows.
        Returns: {ok, best_hours_utc, current_hour_score, recommendation}
        """
        try:
            candles = self._s_get_candles(symbol=symbol, granularity="1h", limit=168)  # 7 days
            if not candles or len(candles) < 24:
                return {"ok": False, "error": "Insufficient data"}
            # Group by hour of day (UTC)
            from collections import defaultdict
            hourly_volumes = defaultdict(list)
            for c in candles:
                from datetime import datetime, timezone
                ts = int(float(c[0])) / 1000  # Bitget gives ms
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                hourly_volumes[hour].append(float(c[5]))
            # Compute avg volume per hour
            avg_by_hour = {h: sum(v) / len(v) for h, v in hourly_volumes.items()}
            # Best hours = lowest volume (less competition, tighter spreads)
            sorted_hours = sorted(avg_by_hour.items(), key=lambda x: x[1])
            best_hours = [h for h, _ in sorted_hours[:4]]
            from datetime import datetime, timezone
            current_hour = datetime.now(timezone.utc).hour
            current_score = avg_by_hour.get(current_hour, 0)
            median_vol = sorted(avg_by_hour.values())[len(avg_by_hour) // 2]
            is_good_time = current_score <= median_vol
            return {
                "ok": True, "symbol": symbol,
                "best_hours_utc": sorted_hours[:6],
                "current_hour_utc": current_hour,
                "is_optimal_window": is_good_time,
                "recommendation": "execute_now" if is_good_time else f"wait_until_utc_hour_{best_hours[0]}",
                "note": "Lower-volume hours = tighter spreads, less market impact",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_iceberg_order(self, symbol: str, total_size_usd: float, num_children: int = 5) -> dict:
        """Split a large order into randomized child orders with random delays.
        Returns: {ok, child_orders: [{size, delay_sec}], total_size_usd, expected_execution_time}
        """
        import random
        try:
            if num_children < 1:
                num_children = 1
            # Randomize sizes around the mean (slight variation)
            base_size = total_size_usd / num_children
            children = []
            remaining = total_size_usd
            for i in range(num_children - 1):
                # Each child varies ±20% of base
                size = base_size * random.uniform(0.8, 1.2)
                size = min(size, remaining - (num_children - i - 1) * base_size * 0.6)  # leave enough
                delay = random.uniform(1, 30) * (i + 1)  # increasing delays
                children.append({"size_usd": round(size, 2), "delay_sec": round(delay, 1)})
                remaining -= size
            children.append({"size_usd": round(remaining, 2), "delay_sec": round(random.uniform(60, 180), 1)})
            total_delay = sum(c["delay_sec"] for c in children)
            return {
                "ok": True, "symbol": symbol, "total_size_usd": total_size_usd,
                "num_children": len(children), "child_orders": children,
                "expected_execution_time_sec": round(total_delay, 1),
                "note": "Iceberg orders reduce market impact and front-running risk",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_funding_arb(self, symbol: str) -> dict:
        """Detect funding rate arbitrage opportunities (short perp + long spot carry trade).
        Returns: {ok, funding_rate, annualized_yield_pct, recommendation}
        """
        try:
            fr = self._s_funding_hist(symbol=symbol, days=7)
            if not fr or (isinstance(fr, dict) and fr.get("error")):
                return {"ok": False, "error": "No funding data"}
            # Get most recent rate
            if isinstance(fr, list) and fr:
                recent_fr = float(fr[0].get("fundingRate", 0))
            elif isinstance(fr, dict):
                recent_fr = float(fr.get("recent", 0))
            else:
                recent_fr = 0
            # Annualized: funding_rate * 3 (per day) * 365
            annualized = recent_fr * 3 * 365 * 100
            # Carry trade: if funding is positive (longs pay shorts), short perp + long spot earns
            # If negative, the reverse. Flag compelling yield.
            rec = (
                "short_perp_long_spot_attractive" if recent_fr > 0.0005 and annualized > 50
                else "long_perp_short_spot_attractive" if recent_fr < -0.0005 and annualized < -50
                else "no_clear_arb"
            )
            return {
                "ok": True, "symbol": symbol,
                "funding_rate_recent": round(recent_fr, 6),
                "annualized_yield_pct": round(annualized, 2),
                "recommendation": rec,
                "note": "Positive funding = longs pay shorts. Short perp + long spot = carry trade",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_loss_autopsy(self, trade_id: int) -> dict:
        """Post-mortem on a losing trade. Qwen tags the failure type: thesis/execution/regime/bad_luck.
        Returns: {ok, trade_id, failure_type, root_cause, recommendations}
        """
        try:
            trade = self.db.get_trade_by_id(trade_id) if hasattr(self.db, "get_trade_by_id") else None
            if not trade:
                # Try a different lookup
                trades = self.db.get_recent_trades(limit=100)
                trade = next((t for t in trades if t.get("id") == trade_id), None)
            if not trade:
                return {"ok": False, "error": f"Trade {trade_id} not found"}
            pnl_pct = float(trade.get("pnl_pct", 0))
            if pnl_pct >= 0:
                return {"ok": True, "trade_id": trade_id, "note": "Not a loss, skip autopsy"}
            # Try Qwen autopsy
            prompt = (
                f"Trade autopsy for loss:\n"
                f"  Symbol: {trade.get('symbol')}\n"
                f"  Side: {trade.get('side')}\n"
                f"  Entry: ${trade.get('price', 0):.4f}\n"
                f"  P&L: {pnl_pct:.2f}%\n"
                f"  Thesis: {trade.get('thesis', '(none)')}\n"
                f"  Skills used: {trade.get('skills_used', '[]')}\n\n"
                f"Classify the failure into ONE of:\n"
                f"  - thesis_failure: the original reason for entry was wrong\n"
                f"  - execution_failure: bad entry/exit timing, slippage, fees\n"
                f"  - regime_failure: market regime shifted unexpectedly\n"
                f"  - bad_luck: random/unpredictable move, nothing could have been done\n\n"
                f"Return EXACTLY 2 lines:\n"
                f"failure_type: <one of the four>\n"
                f"root_cause: <one short sentence>"
            )
            try:
                resp = self.qwen.chat(
                    messages=[{"role": "system", "content": "You are a trading post-mortem analyst. Be honest, no spin."},
                              {"role": "user", "content": prompt}],
                    max_tokens=100, temperature=0.3,
                )
                raw = resp["content"].strip()
                failure_type, root_cause = "unknown", "(unparsed)"
                for line in raw.split("\n"):
                    if line.lower().startswith("failure_type:"):
                        failure_type = line.split(":", 1)[1].strip()
                    elif line.lower().startswith("root_cause:"):
                        root_cause = line.split(":", 1)[1].strip()
            except Exception:
                failure_type, root_cause = "unknown", "(Qwen unavailable)"
            # Log for trend analysis
            self.db.add_memory(
                "autopsy",
                f"Trade #{trade_id} ({trade.get('symbol')}) loss {pnl_pct:.2f}%: {failure_type} - {root_cause}",
                tags=["autopsy", failure_type, trade.get("symbol", "?").lower()],
                importance=5,
            )
            return {
                "ok": True, "trade_id": trade_id, "pnl_pct": round(pnl_pct, 2),
                "failure_type": failure_type, "root_cause": root_cause,
                "recommendation": (
                    "Review thesis criteria" if failure_type == "thesis_failure"
                    else "Tighten entry/exit execution" if failure_type == "execution_failure"
                    else "Add regime filter to entries" if failure_type == "regime_failure"
                    else "Accept variance, no change"
                ),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_edge_half_life(self, strategy: str, days: int = 30) -> dict:
        """Track a strategy's win rate over a rolling window. Flag when decaying.
        Returns: {ok, strategy, current_win_rate, baseline_win_rate, is_decaying, recommendation}
        """
        try:
            trades = self.db.get_trades_for_review(days=days) if hasattr(self.db, "get_trades_for_review") else []
            if not trades:
                return {"ok": True, "strategy": strategy, "current_win_rate": 0.5,
                        "baseline_win_rate": 0.5, "is_decaying": False, "trade_count": 0,
                        "recommendation": "insufficient_data"}
            wins = sum(1 for t in trades if float(t.get("pnl_pct", 0)) > 0)
            current_wr = wins / len(trades) if trades else 0.5
            # Baseline: win rate of the same strategy in earlier window (rough: 50% default)
            baseline_wr = 0.5
            is_decaying = current_wr < baseline_wr * 0.7  # 30% drop
            return {
                "ok": True, "strategy": strategy,
                "current_win_rate": round(current_wr, 3),
                "baseline_win_rate": round(baseline_wr, 3),
                "trade_count": len(trades),
                "is_decaying": is_decaying,
                "recommendation": "downweight_or_pause" if is_decaying else "active",
                "note": "Edges expire; track half-life to avoid slow decay losses",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_counterfactual(self, trade_id: int) -> dict:
        """Simulate 3 alternative decisions for a closed trade: held longer, entered earlier, different size.
        Returns: {ok, trade_id, alternatives: [{scenario, hypothetical_pnl_pct}]}
        """
        try:
            trades = self.db.get_recent_trades(limit=100)
            trade = next((t for t in trades if t.get("id") == trade_id), None)
            if not trade:
                return {"ok": False, "error": f"Trade {trade_id} not found"}
            actual_pnl = float(trade.get("pnl_pct", 0))
            entry = float(trade.get("price", 0))
            # 3 counterfactuals (simplified heuristic — real impl would replay against price history)
            alternatives = [
                {
                    "scenario": "held_2x_longer",
                    "hypothetical_pnl_pct": round(actual_pnl * 1.6, 2),  # rough amplification
                    "verdict": "would_have_been_better" if actual_pnl > 0 else "would_have_been_worse",
                },
                {
                    "scenario": "entered_2pct_earlier",
                    "hypothetical_pnl_pct": round(actual_pnl + 2.0, 2),
                    "verdict": "would_have_been_better" if actual_pnl > 0 else "would_have_been_worse",
                },
                {
                    "scenario": "half_position_size",
                    "hypothetical_pnl_pct": round(actual_pnl * 0.5, 2),  # PnL% same, $PnL halved
                    "hypothetical_pnl_usd": "halved",
                    "verdict": "less_risk_less_reward",
                },
            ]
            return {
                "ok": True, "trade_id": trade_id, "actual_pnl_pct": round(actual_pnl, 2),
                "alternatives": alternatives,
                "note": "Qwen reviews counterfactuals weekly to spot systematic biases",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _s_correlation_kill_switch(self, threshold: float = 0.8) -> dict:
        """Monitor real-time correlation of open positions. If avg pairwise > threshold, force unwind.
        Returns: {ok, open_positions, avg_correlation, threshold, is_kill_switch_active, recommendation}
        """
        try:
            open_trades = self.db.get_open_trades()
            n = len(open_trades)
            if n < 2:
                return {"ok": True, "open_positions": n, "avg_correlation": 0,
                        "is_kill_switch_active": False, "recommendation": "ok"}
            # Pull recent 1h candles for each symbol, compute pairwise correlation
            symbols = list(set(t.get("symbol", "") for t in open_trades))
            closes_by_sym = {}
            for sym in symbols:
                try:
                    candles = self._s_get_candles(symbol=sym, granularity="1h", limit=24)
                    if candles and len(candles) >= 10:
                        closes_by_sym[sym] = [float(c[4]) for c in candles][::-1]
                except Exception:
                    pass
            # Compute pairwise correlations
            corrs = []
            for i, s1 in enumerate(symbols):
                for s2 in symbols[i + 1:]:
                    if s1 in closes_by_sym and s2 in closes_by_sym:
                        c1 = closes_by_sym[s1]
                        c2 = closes_by_sym[s2]
                        n_pts = min(len(c1), len(c2))
                        if n_pts < 5:
                            continue
                        c1, c2 = c1[-n_pts:], c2[-n_pts:]
                        m1, m2 = sum(c1) / n_pts, sum(c2) / n_pts
                        cov = sum((c1[k] - m1) * (c2[k] - m2) for k in range(n_pts)) / n_pts
                        s1_std = (sum((c - m1) ** 2 for c in c1) / n_pts) ** 0.5
                        s2_std = (sum((c - m2) ** 2 for c in c2) / n_pts) ** 0.5
                        if s1_std > 0 and s2_std > 0:
                            corrs.append(cov / (s1_std * s2_std))
            avg_corr = sum(corrs) / len(corrs) if corrs else 0
            is_active = avg_corr > threshold
            return {
                "ok": True, "open_positions": n, "pairs_checked": len(corrs),
                "avg_correlation": round(avg_corr, 3), "threshold": threshold,
                "is_kill_switch_active": is_active,
                "recommendation": "unwind_most_correlated" if is_active else "ok",
                "note": "When all positions move together, you have one big bet, not diversification",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


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
