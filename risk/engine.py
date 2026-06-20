"""
Risk engine — the guardian that prevents Oniṣòwò from doing dumb things.

The 5 risk rules (all configurable, all sized as percentages of account):

1. MAX_TRADE_PCT: don't place any single trade larger than this % of balance
2. MAX_POSITION_PCT: don't let any position be more than this % of portfolio
3. MAX_DRAWDOWN_PCT: if portfolio drops more than this %, kill switch
4. MAX_DAILY_LOSS_PCT: don't lose more than this % in a single day
5. MAX_OPEN_TRADES: don't have more than N open positions
6. BLACKLIST_SYMBOLS: never trade these (e.g., leverage tokens)

The bot's own discretion via suggest_position_size() sizes positions using
ATR + ADX + signal_score + confidence — never a flat dollar amount.

Per-user limits live in the DB settings table (one row per user_id).
A new user's defaults:
  max_trade_pct       25%   — max 25% of balance per trade
  max_position_pct    75%   — max 75% in one asset
  max_drawdown_pct    30%   — kill switch at 30% drawdown
  max_daily_loss_pct  30%   — daily drawdown cap
  max_open_trades     5

A $10 account gets up to $2.50/trade.
A $1,000 account gets up to $250/trade.
A $10,000 account gets up to $2,500/trade.

Users can override via /settings (per-user, persistent).
"""

import os
import json
from typing import Optional
from dataclasses import dataclass, field, asdict


# Sane defaults for a new user. Percentages, not dollars.
DEFAULT_MAX_TRADE_PCT = 0.25        # 25% of balance per trade
DEFAULT_MAX_POSITION_PCT = 0.75     # 75% in one asset
DEFAULT_MAX_DRAWDOWN_PCT = 0.30     # kill switch at 30%
DEFAULT_MAX_DAILY_LOSS_PCT = 0.30   # daily loss cap
DEFAULT_MAX_OPEN_TRADES = 5
DEFAULT_MAX_LEVERAGE = 2
DEFAULT_BLACKLIST = ("USDC",)       # depeg-risk example


@dataclass
class RiskConfig:
    """Per-user risk configuration. All percentages, not dollars.

    Defaults scale with account size:
    - $10 account: max trade $2.50, max daily loss $3.00
    - $1,000 account: max trade $250, max daily loss $300
    - $10,000 account: max trade $2,500, max daily loss $3,000
    """

    # All in percentages of account (0.0–1.0)
    max_trade_pct: float = float(os.environ.get("MAX_TRADE_PCT", DEFAULT_MAX_TRADE_PCT))
    max_position_pct: float = float(os.environ.get("MAX_POSITION_PCT", DEFAULT_MAX_POSITION_PCT))
    max_drawdown_pct: float = float(os.environ.get("MAX_DRAWDOWN_PCT", DEFAULT_MAX_DRAWDOWN_PCT))
    max_daily_loss_pct: float = float(os.environ.get("MAX_DAILY_LOSS_PCT", DEFAULT_MAX_DAILY_LOSS_PCT))

    # Counts (currency-neutral)
    max_open_trades: int = DEFAULT_MAX_OPEN_TRADES
    max_leverage: int = DEFAULT_MAX_LEVERAGE

    # Static blacklist (don't trade these symbols)
    blacklist_symbols: tuple = DEFAULT_BLACKLIST

    # Kill switch state (toggleable via /kill, /release)
    kill_switch_active: bool = False


class RiskEngine:
    """The brain's safety net. Every order goes through this first."""

    def __init__(self, config: Optional[RiskConfig] = None, db=None, user_id: int = 0):
        # Start with defaults, then override from DB if available
        self.config = config or RiskConfig()
        self.db = db
        self.user_id = user_id

        # Load per-user overrides from DB if available
        if db and user_id:
            try:
                self._load_user_overrides()
            except Exception:
                pass  # fall back to defaults

    def _load_user_overrides(self):
        """Load this user's risk overrides from the DB user_settings table."""
        if not self.db or not self.user_id:
            return
        try:
            row = self.db.get_user_setting(self.user_id, "risk_overrides")
        except AttributeError:
            # DB doesn't have get_user_setting yet — fall back gracefully
            return
        if not row:
            return
        try:
            overrides = json.loads(row) if isinstance(row, str) else row
            if "max_trade_pct" in overrides:
                self.config.max_trade_pct = float(overrides["max_trade_pct"])
            if "max_position_pct" in overrides:
                self.config.max_position_pct = float(overrides["max_position_pct"])
            if "max_drawdown_pct" in overrides:
                self.config.max_drawdown_pct = float(overrides["max_drawdown_pct"])
            if "max_daily_loss_pct" in overrides:
                self.config.max_daily_loss_pct = float(overrides["max_daily_loss_pct"])
            if "max_open_trades" in overrides:
                self.config.max_open_trades = int(overrides["max_open_trades"])
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    def save_overrides(self):
        """Persist current config to DB so it survives restarts."""
        if not self.db or not self.user_id:
            return
        overrides = {
            "max_trade_pct": self.config.max_trade_pct,
            "max_position_pct": self.config.max_position_pct,
            "max_drawdown_pct": self.config.max_drawdown_pct,
            "max_daily_loss_pct": self.config.max_daily_loss_pct,
            "max_open_trades": self.config.max_open_trades,
        }
        try:
            self.db.set_user_setting(self.user_id, "risk_overrides", json.dumps(overrides))
        except AttributeError:
            pass  # DB method not available

    @classmethod
    def for_user(cls, user_id: int, db=None) -> "RiskEngine":
        """Factory: build a RiskEngine pre-configured for the given user."""
        return cls(db=db, user_id=user_id)

    # ---------- Helpers ----------

    def max_trade_usd_for(self, balance_usd: float) -> float:
        """Convert the percentage cap into a dollar amount for this balance."""
        if balance_usd <= 0:
            return 0.0
        return round(balance_usd * self.config.max_trade_pct, 2)

    def max_daily_loss_usd_for(self, balance_usd: float) -> float:
        """Convert the daily loss percentage cap into a dollar amount."""
        if balance_usd <= 0:
            return 0.0
        return round(balance_usd * self.config.max_daily_loss_pct, 2)

    # ---------- The actual checks ----------

    def check_order(
        self,
        symbol: str,
        side: str,
        size_usd: float,
        portfolio_value_usd: float,
        open_positions_count: int,
    ) -> tuple[bool, str]:
        """Check if an order is allowed. Returns (allowed, reason).

        All percentage caps scale with the user's account size.
        A $10 account and a $10,000 account both get the same rules
        — just different absolute sizes.
        """
        # Rule 0: Kill switch
        if self.config.kill_switch_active:
            return False, "🛑 Kill switch is active. Use /release to resume trading."

        # Rule 1: Max trade size (% of balance)
        if portfolio_value_usd > 0:
            trade_pct = size_usd / portfolio_value_usd
            if trade_pct > self.config.max_trade_pct:
                return (
                    False,
                    f"❌ Trade size ${size_usd:.2f} is {trade_pct*100:.1f}% of portfolio "
                    f"${portfolio_value_usd:.2f}. Max allowed: "
                    f"{self.config.max_trade_pct*100:.0f}% "
                    f"(${self.max_trade_usd_for(portfolio_value_usd):.2f} for this account). "
                    f"Adjust with `/settings max_trade_pct 50` (or any %).",
                )

        # Rule 2: Max position size (% of portfolio)
        if portfolio_value_usd > 0:
            position_pct = size_usd / portfolio_value_usd
            if position_pct > self.config.max_position_pct:
                return (
                    False,
                    f"❌ Position {position_pct*100:.1f}% exceeds max "
                    f"{self.config.max_position_pct*100:.0f}% of portfolio.",
                )

        # Rule 3: Blacklist
        base = symbol.replace("USDT", "").replace("USDC", "").upper()
        if base in self.config.blacklist_symbols:
            return False, f"❌ {base} is blacklisted. (Default blacklist: leverage tokens, depeg risks)"

        # Rule 4: Max open trades
        if side.lower() == "buy" and open_positions_count >= self.config.max_open_trades:
            return (
                False,
                f"❌ Already {open_positions_count} open positions. Max is {self.config.max_open_trades}.",
            )

        # Rule 5: Minimum size (avoid dust)
        if size_usd < 0.5:
            return False, f"❌ Trade size ${size_usd:.2f} is too small. Minimum is $0.50."

        # All checks passed
        return True, (
            f"✅ Risk check passed. Trade size ${size_usd:.2f}, "
            f"portfolio ${portfolio_value_usd:.2f} "
            f"({(size_usd/portfolio_value_usd*100) if portfolio_value_usd else 0:.1f}% of portfolio, "
            f"under {self.config.max_trade_pct*100:.0f}% cap)."
        )

    def check_drawdown(self, current_value: float, peak_value: float) -> tuple[bool, str]:
        """Check if portfolio has drawn down too much. Returns (safe, status)."""
        if peak_value <= 0:
            return True, "No peak value yet."

        drawdown = (peak_value - current_value) / peak_value

        if drawdown >= self.config.max_drawdown_pct:
            return False, (
                f"🛑 DRAWDOWN KILL: Portfolio down {drawdown*100:.1f}% from peak "
                f"${peak_value:.2f} → ${current_value:.2f}. "
                f"Max allowed: {self.config.max_drawdown_pct*100:.0f}%. "
                f"Trading halted."
            )

        return True, f"Drawdown: {drawdown*100:.1f}% (max allowed: {self.config.max_drawdown_pct*100:.0f}%)"

    def check_daily_loss(self, todays_pnl_usd: float, balance_usd: float) -> tuple[bool, str]:
        """Check if today's losses have exceeded the daily cap."""
        if balance_usd <= 0:
            return True, "No balance to evaluate."
        max_loss = self.max_daily_loss_usd_for(balance_usd)
        if todays_pnl_usd <= -max_loss:
            return False, (
                f"🛑 DAILY LOSS LIMIT: Today's P&L ${todays_pnl_usd:.2f} exceeds "
                f"-${max_loss:.2f} cap ({self.config.max_daily_loss_pct*100:.0f}% of ${balance_usd:.2f}). "
                f"Trading halted for the day."
            )
        return True, f"Today's P&L: ${todays_pnl_usd:.2f} (limit: -${max_loss:.2f})"

    def suggest_position_size(
        self,
        balance_usd: float,
        confidence: float = 0.7,
        signal_score: float = 0.5,
        user_requested_usd: float = None,
    ) -> dict:
        """Compute a reasonable position size given balance, signal strength, and confidence.

        Strategy (now percentage-based, scales with account):
        - Base: balance * max_trade_pct (e.g., 25% of $1000 = $250)
        - Adjust by confidence: low conf = 50% of base, high conf = 100% of base
        - Adjust by signal_score: 0.4 score = 50% of base, 0.8+ score = 100% of base
        - Respect user_requested_usd if explicitly set, but cap at max_trade_pct
        - Never exceed 95% of balance (leave dust for fees)

        Returns: {size_usd, rationale, base, confidence_factor, score_factor}
        """
        if balance_usd <= 0:
            return {"size_usd": 0, "rationale": "No balance available."}

        # Base cap: balance * max_trade_pct (this scales with account)
        base = balance_usd * self.config.max_trade_pct
        # Floor at 5% of balance so we always have a meaningful trade
        base = max(base, balance_usd * 0.05)

        # Confidence scaling: 0.4 conf → 50% of base, 0.85+ conf → 100% of base
        if confidence >= 0.85:
            conf_factor = 1.0
        elif confidence >= 0.4:
            conf_factor = 0.5 + (confidence - 0.4) / 0.45 * 0.5
        else:
            conf_factor = 0.3

        # Signal score scaling
        if signal_score >= 0.8:
            score_factor = 1.0
        elif signal_score >= 0.4:
            score_factor = 0.5 + (signal_score - 0.4) / 0.4 * 0.5
        else:
            score_factor = 0.3

        # Combined adjustment (mean of the two factors)
        adjustment = (conf_factor + score_factor) / 2
        adjusted = base * adjustment

        # If user requested a specific size, respect it but cap at max
        max_allowed = min(
            balance_usd * self.config.max_trade_pct,
            balance_usd * 0.95,
        )
        if user_requested_usd is not None and user_requested_usd > 0:
            final = min(user_requested_usd, max_allowed)
            rationale = (
                f"You asked for ${user_requested_usd:.2f}. "
                f"Capped at ${final:.2f} "
                f"({self.config.max_trade_pct*100:.0f}% of ${balance_usd:.2f} balance = ${max_allowed:.2f}). "
                f"Use `/settings max_trade_pct 100` to lift this cap, or pass a higher amount."
            )
        else:
            final = adjusted
            rationale = (
                f"Bot's discretionary size based on "
                f"balance ${balance_usd:.2f} × max_trade_pct "
                f"{self.config.max_trade_pct*100:.0f}% = base ${base:.2f}, "
                f"adjusted by confidence={confidence:.2f} ({conf_factor:.2f}) "
                f"and signal_score={signal_score:.2f} ({score_factor:.2f}). "
                f"Final: ${final:.2f}."
            )

        # Never exceed 95% of balance
        final = min(final, balance_usd * 0.95)
        # Never go below $1 (Bitget's actual minimum for spot market orders).
        # Round UP to $1 if the computation lands below it, so the order still works
        # on tiny accounts. Only block if even $1 exceeds 95% of balance.
        BITGET_MIN_USDT = 1.0
        if final < BITGET_MIN_USDT:
            if balance_usd >= BITGET_MIN_USDT:
                # Bump up to Bitget's minimum
                return {
                    "size_usd": round(BITGET_MIN_USDT, 2),
                    "rationale": (
                        f"Computed size ${final:.2f} was below Bitget's minimum order size. "
                        f"Rounded up to ${BITGET_MIN_USDT:.2f}. "
                        f"Your balance is ${balance_usd:.2f} — fund more to enable larger trades."
                    ),
                    "base": round(BITGET_MIN_USDT, 2),
                    "confidence_factor": 0,
                    "score_factor": 0,
                    "pct_of_balance": round(BITGET_MIN_USDT / balance_usd * 100, 1) if balance_usd > 0 else 0,
                }
            return {
                "size_usd": 0,
                "rationale": (
                    f"Balance ${balance_usd:.2f} is below Bitget's minimum order size of ${BITGET_MIN_USDT:.2f}. "
                    f"Fund your account with at least ${BITGET_MIN_USDT:.2f} to trade."
                ),
            }
        return {
            "size_usd": round(final, 2),
            "rationale": (
                f"Computed size ${final:.2f} for balance ${balance_usd:.2f}. "
                f"Capped at 95% of balance."
                    f"Your balance is ${balance_usd:.2f} — try a smaller account or fund more."
                ),
            }

        return {
            "size_usd": round(final, 2),
            "rationale": rationale,
            "base": round(base, 2),
            "confidence_factor": round(conf_factor, 2),
            "score_factor": round(score_factor, 2),
            "pct_of_balance": round(final / balance_usd * 100, 1) if balance_usd > 0 else 0,
        }

    def activate_kill_switch(self, reason: str = "Manual"):
        """Activate the kill switch. No more trades until released."""
        self.config.kill_switch_active = True
        if self.db:
            self.db.add_memory(
                "rule",
                f"Kill switch activated: {reason}",
                tags=["risk", "kill_switch"],
                importance=10,
            )

    def release_kill_switch(self):
        """Release the kill switch."""
        self.config.kill_switch_active = False
        if self.db:
            self.db.add_memory(
                "rule",
                "Kill switch released by user",
                tags=["risk", "kill_switch"],
                importance=10,
            )

    def get_status(self, balance_usd: float = 0.0) -> dict:
        """Get current risk engine status for display. Pass balance to show dollar values."""
        status = {
            "max_trade_pct": f"{self.config.max_trade_pct*100:.0f}%",
            "max_position_pct": f"{self.config.max_position_pct*100:.0f}%",
            "max_drawdown_pct": f"{self.config.max_drawdown_pct*100:.0f}%",
            "max_daily_loss_pct": f"{self.config.max_daily_loss_pct*100:.0f}%",
            "max_open_trades": self.config.max_open_trades,
            "max_leverage": self.config.max_leverage,
            "kill_switch_active": self.config.kill_switch_active,
            "blacklist": list(self.config.blacklist_symbols),
        }
        if balance_usd > 0:
            status["max_trade_usd_for_this_balance"] = self.max_trade_usd_for(balance_usd)
            status["max_daily_loss_usd_for_this_balance"] = self.max_daily_loss_usd_for(balance_usd)
        return status

    def update_limits(
        self,
        max_trade_pct: Optional[float] = None,
        max_position_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        max_daily_loss_pct: Optional[float] = None,
        max_open_trades: Optional[int] = None,
        persist: bool = True,
    ):
        """Update risk limits. Called by /settings command. Persists to DB."""
        if max_trade_pct is not None:
            # Accept both 0.25 and "25" forms
            v = float(max_trade_pct)
            if v > 1:
                v = v / 100  # treat "25" as 25%
            self.config.max_trade_pct = max(0.01, min(v, 1.0))  # clamp 1%-100%
        if max_position_pct is not None:
            v = float(max_position_pct)
            if v > 1:
                v = v / 100
            self.config.max_position_pct = max(0.01, min(v, 1.0))
        if max_drawdown_pct is not None:
            v = float(max_drawdown_pct)
            if v > 1:
                v = v / 100
            self.config.max_drawdown_pct = max(0.05, min(v, 1.0))
        if max_daily_loss_pct is not None:
            v = float(max_daily_loss_pct)
            if v > 1:
                v = v / 100
            self.config.max_daily_loss_pct = max(0.05, min(v, 1.0))
        if max_open_trades is not None:
            self.config.max_open_trades = max(1, min(int(max_open_trades), 50))

        if persist:
            self.save_overrides()

        if self.db:
            self.db.add_memory(
                "preference",
                f"Risk limits updated: trade={self.config.max_trade_pct*100:.0f}%, "
                f"position={self.config.max_position_pct*100:.0f}%, "
                f"drawdown={self.config.max_drawdown_pct*100:.0f}%, "
                f"daily_loss={self.config.max_daily_loss_pct*100:.0f}%, "
                f"open={self.config.max_open_trades}",
                tags=["risk", "settings"],
                importance=7,
            )
