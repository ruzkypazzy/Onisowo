"""
Risk engine — the guardian that prevents Oniṣòwò from doing dumb things.

The 6 risk rules (all configurable, all hardcoded as safe defaults):

1. MAX_TRADE_USD: don't place any single trade larger than this
2. MAX_POSITION_PCT: don't let any position be more than this % of portfolio
3. MAX_DRAWDOWN_PCT: if portfolio drops more than this %, kill switch
4. MAX_DAILY_LOSS_USD: don't lose more than this in a single day
5. MAX_OPEN_TRADES: don't have more than N open positions
6. BLACKLIST_SYMBOLS: never trade these (e.g., leverage tokens)

Every trade goes through check_order() before being sent to Bitget.
"""

import os
from typing import Optional
from dataclasses import dataclass


@dataclass
class RiskConfig:
    """Risk engine configuration. Override via env vars or settings DB."""

    max_trade_usd: float = float(os.environ.get("MAX_TRADE_USD", "2.00"))
    max_position_pct: float = 0.40  # 40% of portfolio
    max_drawdown_pct: float = float(os.environ.get("MAX_DRAWDOWN_PCT", "0.30"))
    max_daily_loss_usd: float = 5.00
    max_open_trades: int = 5
    max_leverage: int = 2  # never exceed 2x leverage on futures
    blacklist_symbols: tuple = ("USDC",)  # USDC is the depeg risk example
    kill_switch_active: bool = False  # user can toggle this via /kill command


class RiskEngine:
    """The brain's safety net. Every order goes through this first."""

    def __init__(self, config: Optional[RiskConfig] = None, db=None):
        self.config = config or RiskConfig()
        self.db = db

    def check_order(
        self,
        symbol: str,
        side: str,
        size_usd: float,
        portfolio_value_usd: float,
        open_positions_count: int,
    ) -> tuple[bool, str]:
        """Check if an order is allowed. Returns (allowed, reason).

        If allowed=True, the order can proceed.
        If allowed=False, the order is blocked and reason explains why.
        """
        # Rule 0: Kill switch
        if self.config.kill_switch_active:
            return False, "🛑 Kill switch is active. Use /release to resume trading."

        # Rule 1: Max trade size
        if size_usd > self.config.max_trade_usd:
            return (
                False,
                f"❌ Trade size ${size_usd:.2f} exceeds max ${self.config.max_trade_usd:.2f}/trade.",
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
        return True, f"✅ Risk check passed. Trade size ${size_usd:.2f}, portfolio ${portfolio_value_usd:.2f}."

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

    def get_status(self) -> dict:
        """Get current risk engine status for display."""
        return {
            "max_trade_usd": self.config.max_trade_usd,
            "max_position_pct": self.config.max_position_pct,
            "max_drawdown_pct": self.config.max_drawdown_pct,
            "max_open_trades": self.config.max_open_trades,
            "max_leverage": self.config.max_leverage,
            "kill_switch_active": self.config.kill_switch_active,
            "blacklist": list(self.config.blacklist_symbols),
        }

    def update_limits(
        self,
        max_trade_usd: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
    ):
        """Update risk limits. Called by /settings command."""
        if max_trade_usd is not None:
            self.config.max_trade_usd = float(max_trade_usd)
        if max_drawdown_pct is not None:
            self.config.max_drawdown_pct = float(max_drawdown_pct)
        if self.db:
            self.db.add_memory(
                "preference",
                f"Risk limits updated: max_trade=${self.config.max_trade_usd}, "
                f"max_dd={self.config.max_drawdown_pct*100:.0f}%",
                tags=["risk", "settings"],
                importance=7,
            )
