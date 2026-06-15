"""
The Strategist — Oniṣòwò's autonomous trading runtime.

This is what makes Oniṣòwò an AGENT, not a chat wrapper.

Runs a tick loop in a background thread. On every tick:

  1. EVALUATE  — for each open position, decide: hold, take-profit-early,
                 cut-loss, trail-stop, or let SL/TP hit naturally
  2. SCAN      — for each watched symbol, look for new entry signals
                 (RSI oversold, funding-rate extreme, MEV-safe counterparty)
  3. EXECUTE   — place orders via Bitget (only if risk engine approves)
  4. REFLECT   — log every decision, write journal memory
  5. ALERT     — Telegram notification (no prompt, no permission needed)

The adaptive TP/SL logic is the headline feature:
- 5% gain with 10% target → check if thesis is still valid
- If thesis_decay > 0.7 (the original reason for entry is gone) → close early
- If momentum is fading AND we'd give back the gain → close early
- If we hit SL → close (defensive)
- If we hit TP → close (target reached)
- Otherwise → hold
"""

import os
import json
import time
import logging
import threading
from typing import Optional
from dataclasses import dataclass, field

from clients.bitget import BitgetClient, BitgetAPIError
from clients.qwen import QwenClient
from db.database import Database
from risk.engine import RiskEngine

logger = logging.getLogger(__name__)


# Decision codes returned by the adaptive TP/SL matrix
HOLD = "HOLD"
CLOSE_TP = "CLOSE_TP"            # target reached
CLOSE_SL = "CLOSE_SL"            # stop loss hit
CLOSE_EARLY_TP = "CLOSE_EARLY_TP"  # take profit before target because thesis is dying
CLOSE_CUT_LOSS = "CLOSE_CUT_LOSS"  # cut loss early because thesis is dead and pnl is bad
TRAIL_STOP = "TRAIL_STOP"        # move SL to breakeven and let it ride


@dataclass
class StrategistConfig:
    """User-configurable strategy rules. Set once via /strategy command."""
    # Which symbols the strategist watches for new entries
    watchlist: list = field(default_factory=lambda: ["SOLUSDT", "BTCUSDT", "ETHUSDT"])
    # Maximum number of open positions at once
    max_open_positions: int = 3
    # Default trade size in USDT
    trade_size_usdt: float = 1.0
    # Default take-profit percent
    default_tp_pct: float = 10.0
    # Default stop-loss percent
    default_sl_pct: float = 5.0
    # How often the loop runs (seconds)
    tick_seconds: int = 30
    # Whether to autonomously place entries (if False, alerts only)
    auto_enter: bool = True
    # Whether to autonomously manage exits (if False, alerts only)
    auto_exit: bool = True
    # RSI oversold threshold for entry
    rsi_oversold: float = 30.0
    # Funding rate extreme (negative = shorts paying longs, often a bottom signal)
    funding_extreme: float = -0.05
    # Minimum confluence (number of signals that must agree to enter)
    min_confluence: int = 2


@dataclass
class TickDecision:
    """A single decision made during a tick."""
    timestamp: float
    decision: str
    symbol: str
    trade_id: Optional[int]
    reasoning: str
    metrics: dict = field(default_factory=dict)


class Strategist:
    """The autonomous trading runtime."""

    def __init__(
        self,
        bitget: BitgetClient,
        qwen: QwenClient,
        db: Database,
        risk: RiskEngine,
        skills_registry=None,
        config: Optional[StrategistConfig] = None,
    ):
        self.bitget = bitget
        self.qwen = qwen
        self.db = db
        self.risk = risk
        self.skills = skills_registry
        self.config = config or StrategistConfig()

        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._running = False

        # Recent decisions (for /strategist status)
        self.recent_decisions: list[TickDecision] = []
        # Last tick timestamp
        self.last_tick: float = 0.0
        # Total ticks run
        self.ticks = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the background tick loop. Idempotent."""
        if self._running:
            return False
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="Strategist")
        self._thread.start()
        self._running = True
        logger.info("Strategist started (tick every %ds, watchlist=%s)", self.config.tick_seconds, self.config.watchlist)
        return True

    def stop(self):
        """Stop the loop. Returns after the thread has exited."""
        if not self._running:
            return False
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._running = False
        logger.info("Strategist stopped")
        return True

    @property
    def is_running(self) -> bool:
        return self._running

    def _run_loop(self):
        """Main loop. Catches all errors so the thread never dies silently."""
        while not self._stop_flag.is_set():
            try:
                self.tick()
            except Exception as e:
                logger.exception(f"Strategist tick failed: {e}")
            # Sleep with periodic wake-ups so /strategist stop is responsive
            for _ in range(self.config.tick_seconds):
                if self._stop_flag.is_set():
                    break
                time.sleep(1)

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def tick(self) -> list[TickDecision]:
        """Run one pass: EVALUATE → SCAN → EXECUTE → REFLECT. Returns decisions made."""
        self.ticks += 1
        self.last_tick = time.time()
        decisions: list[TickDecision] = []

        # 1. EVALUATE — for each open position, decide whether to hold, take-profit-early, etc.
        open_trades = self.db.get_open_trades()
        for trade in open_trades:
            try:
                decision = self._evaluate_position(trade)
                if decision:
                    decisions.append(decision)
                    if decision.decision != HOLD and self.config.auto_exit:
                        self._execute_exit(decision)
            except Exception as e:
                logger.exception(f"Evaluate failed for trade {trade.get('id')}: {e}")

        # 2. SCAN — for each watched symbol, look for new entry signals
        if len(open_trades) < self.config.max_open_positions:
            for symbol in self.config.watchlist:
                try:
                    decision = self._scan_for_entry(symbol, open_trades)
                    if decision and decision.decision == "ENTER":
                        decisions.append(decision)
                        if self.config.auto_enter:
                            self._execute_entry(decision)
                except Exception as e:
                    logger.exception(f"Scan failed for {symbol}: {e}")

        # 3. REFLECT — keep memory of recent decisions (cap at 50)
        self.recent_decisions = (self.recent_decisions + decisions)[-50:]

        return decisions

    # ------------------------------------------------------------------
    # Adaptive TP/SL — the headline feature
    # ------------------------------------------------------------------

    def _evaluate_position(self, trade: dict) -> Optional[TickDecision]:
        """Run the adaptive TP/SL decision matrix on a single open position.

        Returns a TickDecision. The decision field is one of:
        HOLD, CLOSE_TP, CLOSE_SL, CLOSE_EARLY_TP, CLOSE_CUT_LOSS, TRAIL_STOP.
        """
        trade_id = trade.get("id")
        symbol = trade.get("symbol", "")
        side = trade.get("side", "buy")
        entry_price = float(trade.get("price", 0))
        tp_pct = float(trade.get("tp_pct", self.config.default_tp_pct))
        sl_pct = float(trade.get("sl_pct", self.config.default_sl_pct))
        thesis = trade.get("thesis", "")

        if entry_price <= 0:
            return None

        # Get current price
        try:
            ticker = self.bitget.get_ticker(symbol)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            current_price = float(ticker.get("lastPr", 0))
        except Exception as e:
            logger.exception(f"get_ticker failed for {symbol}: {e}")
            return None

        if current_price <= 0:
            return None

        # Compute P&L pct
        if side == "buy":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:  # short
            pnl_pct = (entry_price - current_price) / entry_price * 100

        # Compute metrics
        tp_target_price = entry_price * (1 + tp_pct / 100) if side == "buy" else entry_price * (1 - tp_pct / 100)
        sl_target_price = entry_price * (1 - sl_pct / 100) if side == "buy" else entry_price * (1 + sl_pct / 100)
        tp_progress = pnl_pct / tp_pct if tp_pct > 0 else 0  # 0-1, 1 = target hit

        metrics = {
            "entry_price": entry_price,
            "current_price": current_price,
            "pnl_pct": round(pnl_pct, 3),
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "tp_target_price": tp_target_price,
            "sl_target_price": sl_target_price,
            "tp_progress": round(tp_progress, 3),
        }

        # 1. SL hit
        if (side == "buy" and current_price <= sl_target_price) or (side == "sell" and current_price >= sl_target_price):
            return TickDecision(
                timestamp=time.time(),
                decision=CLOSE_SL,
                symbol=symbol,
                trade_id=trade_id,
                reasoning=f"Stop loss hit at ${current_price:.4f} (SL target ${sl_target_price:.4f}, pnl {pnl_pct:+.2f}%)",
                metrics=metrics,
            )

        # 2. TP hit
        if (side == "buy" and current_price >= tp_target_price) or (side == "sell" and current_price <= tp_target_price):
            return TickDecision(
                timestamp=time.time(),
                decision=CLOSE_TP,
                symbol=symbol,
                trade_id=trade_id,
                reasoning=f"Take profit target hit at ${current_price:.4f} (TP target ${tp_target_price:.4f}, pnl {pnl_pct:+.2f}%)",
                metrics=metrics,
            )

        # For the adaptive logic, we need momentum + thesis_decay.
        # Compute them now (only when not at extremes).
        momentum = self._compute_momentum(symbol)
        thesis_decay = self._compute_thesis_decay(symbol, side, thesis, entry_price)
        tp_reachable = momentum * (1.0 - thesis_decay)

        metrics["momentum"] = round(momentum, 3)
        metrics["thesis_decay"] = round(thesis_decay, 3)
        metrics["tp_reachable"] = round(tp_reachable, 3)

        # 3. EARLY TAKE PROFIT — the headline behavior
        # "We're at 5% with 10% target, but the move that got us here is fading,
        # and the original thesis (e.g. oversold bounce) has decayed"
        if tp_progress > 0.3 and pnl_pct > 0 and tp_reachable < 0.3:
            return TickDecision(
                timestamp=time.time(),
                decision=CLOSE_EARLY_TP,
                symbol=symbol,
                trade_id=trade_id,
                reasoning=(
                    f"Adaptive early-TP: we're at {pnl_pct:+.2f}% (TP target {tp_pct:+.1f}%, progress {tp_progress:.0%}). "
                    f"Momentum={momentum:.2f}, thesis_decay={thesis_decay:.2f}, TP-reachable={tp_reachable:.2f}. "
                    f"Thesis is dying and we'd likely give back the gain. Locking in {pnl_pct:+.2f}%."
                ),
                metrics=metrics,
            )

        # 4. CUT LOSS — thesis dead, pnl negative, no point waiting for SL
        if thesis_decay > 0.7 and pnl_pct < 0:
            return TickDecision(
                timestamp=time.time(),
                decision=CLOSE_CUT_LOSS,
                symbol=symbol,
                trade_id=trade_id,
                reasoning=(
                    f"Cutting loss early: pnl {pnl_pct:+.2f}%, thesis_decay={thesis_decay:.2f}. "
                    f"Original entry thesis no longer valid; better to exit now than wait for SL."
                ),
                metrics=metrics,
            )

        # 5. TRAIL STOP — thesis partially decayed, move SL to breakeven
        if thesis_decay > 0.5 and pnl_pct > 0 and tp_progress < 0.8:
            return TickDecision(
                timestamp=time.time(),
                decision=TRAIL_STOP,
                symbol=symbol,
                trade_id=trade_id,
                reasoning=(
                    f"Trailing stop to breakeven: pnl {pnl_pct:+.2f}%, thesis_decay={thesis_decay:.2f}. "
                    f"Locking in breakeven and giving the trade room to either run to TP or exit flat."
                ),
                metrics=metrics,
            )

        # 6. Default: HOLD
        return TickDecision(
            timestamp=time.time(),
            decision=HOLD,
            symbol=symbol,
            trade_id=trade_id,
            reasoning=f"Holding: pnl {pnl_pct:+.2f}%, momentum={momentum:.2f}, thesis_decay={thesis_decay:.2f}",
            metrics=metrics,
        )

    def _compute_momentum(self, symbol: str) -> float:
        """Compute 0-1 momentum score from candles, funding, RSI.

        Returns 1.0 = strong momentum in our favor, 0.0 = dead.
        Uses the skills registry if available, falls back to direct Bitget.
        """
        try:
            # Try to use the skills registry first
            if self.skills is not None:
                rsi_result = self.skills.invoke("rsi", {"symbol": symbol, "period": 14})
                if rsi_result.get("ok"):
                    rsi = float(rsi_result.get("result", {}).get("rsi", 50))
                else:
                    rsi = 50.0
            else:
                rsi = 50.0
        except Exception:
            rsi = 50.0

        # RSI 30-70 = neutral, <30 oversold (good for long), >70 overbought (good for short exit)
        # For long positions: lower RSI = more upside potential BUT if RSI < 30 we already entered
        # So "momentum fading" = RSI rising toward 50+ (taking profits) or volume drying up
        if rsi < 30:
            return 0.9  # deep oversold = strong potential
        elif rsi < 45:
            return 0.7  # recovery zone
        elif rsi < 55:
            return 0.5  # neutral
        elif rsi < 70:
            return 0.3  # overbought zone
        else:
            return 0.1  # deep overbought = exhausted

    def _compute_thesis_decay(self, symbol: str, side: str, thesis: str, entry_price: float) -> float:
        """0-1, how much the original entry thesis has decayed.

        If the thesis was 'RSI oversold bounce' and RSI is now 70 → high decay.
        If the thesis was 'funding rate extreme' and funding is now 0 → high decay.
        """
        decay = 0.0  # default: thesis intact

        if not thesis:
            # No specific thesis = generic time-based decay
            return 0.3

        # Check RSI vs entry-time RSI
        try:
            if self.skills is not None:
                rsi_result = self.skills.invoke("rsi", {"symbol": symbol, "period": 14})
                if rsi_result.get("ok"):
                    rsi = float(rsi_result.get("result", {}).get("rsi", 50))
                else:
                    return 0.3
            else:
                return 0.3
        except Exception:
            return 0.3

        # For long entries on oversold thesis
        if "oversold" in thesis.lower() or "rsi" in thesis.lower():
            if side == "buy":
                # The thesis was "buy because RSI<30". If RSI is now 70, thesis is fully decayed.
                if rsi > 70:
                    decay = max(decay, 0.9)
                elif rsi > 60:
                    decay = max(decay, 0.7)
                elif rsi > 50:
                    decay = max(decay, 0.4)
                elif rsi > 40:
                    decay = max(decay, 0.2)
                else:
                    decay = max(decay, 0.0)
            else:  # short
                if rsi < 30:
                    decay = max(decay, 0.9)
                elif rsi < 40:
                    decay = max(decay, 0.7)
                else:
                    decay = max(decay, 0.2)

        return decay

    # ------------------------------------------------------------------
    # Entry signal scanning
    # ------------------------------------------------------------------

    def _scan_for_entry(self, symbol: str, open_trades: list) -> Optional[TickDecision]:
        """Look for entry signals on a symbol. Returns a decision with action='ENTER' or None."""
        # Skip if we already have a position in this symbol
        for t in open_trades:
            if t.get("symbol") == symbol:
                return None

        signals = {}
        confluence = 0

        # 1. RSI oversold
        try:
            rsi_val = None
            if self.skills is not None:
                r = self.skills.invoke("rsi", {"symbol": symbol, "period": 14})
                if r.get("ok"):
                    rsi_val = float(r.get("result", {}).get("rsi", 50))
            if rsi_val is not None and rsi_val < self.config.rsi_oversold:
                signals["rsi_oversold"] = f"RSI={rsi_val:.1f} < {self.config.rsi_oversold}"
                confluence += 1
        except Exception:
            pass

        # 2. Funding rate extreme
        try:
            if self.skills is not None:
                fr = self.skills.invoke("funding_rate_history", {"symbol": symbol, "days": 1})
                if fr.get("ok"):
                    fr_data = fr.get("result", {})
                    # Get the most recent funding rate
                    if isinstance(fr_data, list) and fr_data:
                        recent_fr = float(fr_data[0].get("fundingRate", 0))
                    elif isinstance(fr_data, dict):
                        recent_fr = float(fr_data.get("recent", 0))
                    else:
                        recent_fr = 0
                    if recent_fr < self.config.funding_extreme / 100:
                        signals["funding_extreme"] = f"funding={recent_fr:.4f} < {self.config.funding_extreme/100:.4f}"
                        confluence += 1
        except Exception:
            pass

        # 3. MEV safety check (skip MEV-exposed symbols for spot)
        try:
            if self.skills is not None:
                mev = self.skills.invoke("mev_exposure_check", {"symbol": symbol})
                if mev.get("ok"):
                    mev_data = mev.get("result", {})
                    risk = mev_data.get("risk_level", "unknown")
                    if risk in ("low", "minimal"):
                        signals["mev_safe"] = f"MEV risk={risk}"
                        confluence += 1
        except Exception:
            pass

        if confluence < self.config.min_confluence:
            return None

        # Build a thesis string for this entry
        thesis = " + ".join(signals.values())
        reasoning = f"Confluence entry ({confluence} signals): {thesis}"

        return TickDecision(
            timestamp=time.time(),
            decision="ENTER",
            symbol=symbol,
            trade_id=None,
            reasoning=reasoning,
            metrics={"confluence": confluence, "signals": signals},
        )

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def _execute_exit(self, decision: TickDecision):
        """Place the exit order and update the DB."""
        trade_id = decision.trade_id
        if not trade_id:
            return

        try:
            trade = next((t for t in self.db.get_open_trades() if t.get("id") == trade_id), None)
            if not trade:
                return

            symbol = trade["symbol"]
            entry_side = trade["side"]
            size = float(trade.get("size", 0))

            # Opposite side to close
            close_side = "sell" if entry_side == "buy" else "buy"

            # Get current price for P&L
            current_price = decision.metrics.get("current_price", 0)
            entry_price = float(trade.get("price", 0))
            if entry_price > 0 and current_price > 0:
                if entry_side == "buy":
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:
                    pnl_pct = (entry_price - current_price) / entry_price * 100
                pnl_usd = float(trade.get("quote_usd", 0)) * (pnl_pct / 100)
            else:
                pnl_pct = 0
                pnl_usd = 0

            # Place the market order to close
            order = self.bitget.place_spot_order(
                symbol=symbol,
                side=close_side,
                order_type="market",
                size=str(size) if size > 0 else None,
                quote_size=None,
            )
            order_id = order.get("orderId", "")

            # Update DB
            self.db.close_trade(trade_id, current_price, pnl_usd, pnl_pct)
            if decision.decision == CLOSE_EARLY_TP:
                self.db.increment_early_close(trade_id)

            # Reflect
            self.db.add_memory(
                "observation",
                f"Strategist closed trade {trade_id} ({symbol}) via {decision.decision}: "
                f"pnl {pnl_pct:+.2f}% (${pnl_usd:+.3f}). Reason: {decision.reasoning[:200]}",
                tags=["strategist", "exit", decision.decision.lower(), symbol],
                importance=5,
            )

            logger.info(f"Strategist exit: {decision.decision} {symbol} pnl={pnl_pct:+.2f}% orderId={order_id}")
        except BitgetAPIError as e:
            logger.exception(f"Strategist exit failed (Bitget error): {e}")
        except Exception as e:
            logger.exception(f"Strategist exit failed: {e}")

    def _execute_entry(self, decision: TickDecision):
        """Place the entry order after risk check."""
        try:
            symbol = decision.symbol
            amount_usd = self.config.trade_size_usdt

            # Risk check first
            portfolio = self.bitget.get_portfolio_value_usdt()
            open_positions = len(self.db.get_open_trades())
            allowed, reason = self.risk.check_order(
                symbol=symbol,
                side="buy",
                size_usd=amount_usd,
                portfolio_value_usd=portfolio,
                open_positions_count=open_positions,
            )
            if not allowed:
                logger.info(f"Strategist entry blocked by risk engine: {reason}")
                return

            # Get current price
            ticker = self.bitget.get_ticker(symbol)
            if isinstance(ticker, list) and ticker:
                ticker = ticker[0]
            price = float(ticker.get("lastPr", 0))
            if price <= 0:
                return

            # Place the order
            order = self.bitget.place_spot_order(
                symbol=symbol,
                side="buy",
                order_type="market",
                quote_size=str(amount_usd),
            )
            order_id = order.get("orderId", "")
            size = amount_usd / price if price > 0 else 0

            # Record
            self.db.record_trade(
                symbol=symbol,
                side="buy",
                order_type="spot",
                size=size,
                price=price,
                quote_usd=amount_usd,
                order_id=order_id,
                reason=decision.reasoning,
                skills_used=["strategist_tick", "rsi", "funding_rate_history", "mev_exposure_check"],
                confidence=0.7,
                tp_pct=self.config.default_tp_pct,
                sl_pct=self.config.default_sl_pct,
                thesis=decision.reasoning,
                entry_signals=decision.metrics.get("signals", {}),
            )

            # Reflect
            self.db.add_memory(
                "observation",
                f"Strategist opened position: BUY ${amount_usd:.2f} {symbol} @ ${price:.4f}. "
                f"TP={self.config.default_tp_pct}% SL={self.config.default_sl_pct}%. "
                f"Thesis: {decision.reasoning[:200]}",
                tags=["strategist", "entry", symbol],
                importance=4,
            )

            logger.info(f"Strategist entry: BUY ${amount_usd:.2f} {symbol} @ ${price:.4f} orderId={order_id}")
        except BitgetAPIError as e:
            logger.exception(f"Strategist entry failed (Bitget error): {e}")
        except Exception as e:
            logger.exception(f"Strategist entry failed: {e}")

    # ------------------------------------------------------------------
    # Status & config
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Snapshot of the strategist's current state (for /strategist status)."""
        return {
            "running": self._running,
            "ticks": self.ticks,
            "last_tick": self.last_tick,
            "watchlist": self.config.watchlist,
            "trade_size_usdt": self.config.trade_size_usdt,
            "tp_pct": self.config.default_tp_pct,
            "sl_pct": self.config.default_sl_pct,
            "auto_enter": self.config.auto_enter,
            "auto_exit": self.config.auto_exit,
            "tick_seconds": self.config.tick_seconds,
            "recent_decisions": [
                {
                    "timestamp": d.timestamp,
                    "decision": d.decision,
                    "symbol": d.symbol,
                    "trade_id": d.trade_id,
                    "reasoning": d.reasoning[:200],
                }
                for d in self.recent_decisions[-10:]
            ],
        }
