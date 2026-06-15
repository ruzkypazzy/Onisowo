"""
Database — SQLite for trade journal, portfolio state, recursive-improvement memory.

Single-file SQLite, no external DB needed. Just works on any VPS.

Tables:
- trades: every trade (open + close)
- portfolio_snapshots: daily portfolio value tracking
- signals: trade signals (with reasoning) before they're executed
- memory: free-form notes from the agent (for recursive self-improvement)
- settings: per-user config (kill switch, max trade, etc.)
"""

import os
import sqlite3
import json
import time
from contextlib import contextmanager
from typing import Optional
from datetime import datetime


class Database:
    """SQLite database for Oniṣòwò."""

    SCHEMA = """
    -- Trade journal: every entry and exit
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,           -- "buy" or "sell"
        order_type TEXT NOT NULL,     -- "spot" or "futures"
        size REAL,
        price REAL,
        quote_usd REAL,               -- USD value at trade time
        order_id TEXT,                -- Bitget order ID
        reason TEXT,                  -- agent's reasoning
        skills_used TEXT,             -- JSON array of skill names
        confidence REAL,              -- 0-1, agent's confidence in this trade
        status TEXT DEFAULT 'open',   -- "open" | "closed" | "cancelled"
        pnl_usd REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0,
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP
    );

    -- Portfolio snapshots: daily value
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        total_value_usd REAL,
        cash_usd REAL,
        positions_json TEXT,          -- JSON: [{symbol, size, value_usd}, ...]
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Trade signals: pre-execution thoughts
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        action TEXT,                  -- "buy" | "sell" | "hold"
        reasoning TEXT,               -- the agent's full chain of thought
        skills_invoked TEXT,          -- JSON array
        market_state TEXT,            -- JSON snapshot of market at signal time
        executed BOOLEAN DEFAULT 0,
        trade_id INTEGER,             -- FK to trades.id
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Agent memory: long-term notes for recursive self-improvement
    CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,                -- "lesson" | "observation" | "rule" | "preference"
        content TEXT NOT NULL,
        tags TEXT,                    -- JSON array
        importance INTEGER DEFAULT 5, -- 1-10
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Settings: per-user config
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("DATABASE_PATH", "./db/onisowo.db")

        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)

        self._init_schema()

    def _init_schema(self):
        """Create all tables if they don't exist."""
        with self._conn() as conn:
            conn.executescript(self.SCHEMA)
            conn.commit()

    @contextmanager
    def _conn(self):
        """Context manager for DB connections. Auto-commits on success, rolls back on error."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Trades
    # -------------------------------------------------------------------------

    def record_trade(
        self,
        symbol: str,
        side: str,
        order_type: str = "spot",
        size: float = 0,
        price: float = 0,
        quote_usd: float = 0,
        order_id: str = "",
        reason: str = "",
        skills_used: list = None,
        confidence: float = 0.0,
    ) -> int:
        """Record a trade. Returns the trade ID."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades (symbol, side, order_type, size, price, quote_usd,
                                       order_id, reason, skills_used, confidence, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
                (
                    symbol,
                    side,
                    order_type,
                    size,
                    price,
                    quote_usd,
                    order_id,
                    reason,
                    json.dumps(skills_used or []),
                    confidence,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, pnl_usd: float, pnl_pct: float):
        """Mark a trade as closed."""
        with self._conn() as conn:
            conn.execute(
                """UPDATE trades
                   SET status = 'closed',
                       price = ?,
                       pnl_usd = ?,
                       pnl_pct = ?,
                       closed_at = ?
                   WHERE id = ?""",
                (exit_price, pnl_usd, pnl_pct, datetime.utcnow().isoformat(), trade_id),
            )
            conn.commit()

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Get recent trades (most recent first)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_open_trades(self) -> list[dict]:
        """Get all open trades."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open' ORDER BY opened_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_trades_for_review(self, days: int = 7) -> list[dict]:
        """Get closed trades in the last N days (for weekly review)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM trades
                   WHERE status = 'closed'
                     AND closed_at > datetime('now', ?)
                   ORDER BY closed_at DESC""",
                (f"-{days} days",),
            ).fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Signals (pre-trade thoughts)
    # -------------------------------------------------------------------------

    def record_signal(
        self,
        symbol: str,
        action: str,
        reasoning: str,
        skills_invoked: list,
        market_state: dict,
        trade_id: Optional[int] = None,
    ) -> int:
        """Record a trade signal (whether or not it was executed)."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO signals (symbol, action, reasoning, skills_invoked,
                                        market_state, trade_id, executed)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    action,
                    reasoning,
                    json.dumps(skills_invoked),
                    json.dumps(market_state),
                    trade_id,
                    1 if trade_id else 0,
                ),
            )
            conn.commit()
            return cur.lastrowid

    # -------------------------------------------------------------------------
    # Memory (recursive self-improvement)
    # -------------------------------------------------------------------------

    def add_memory(self, category: str, content: str, tags: list = None, importance: int = 5) -> int:
        """Add a memory entry. Returns the memory ID."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO memory (category, content, tags, importance)
                   VALUES (?, ?, ?, ?)""",
                (category, content, json.dumps(tags or []), importance),
            )
            conn.commit()
            return cur.lastrowid

    def get_memories(self, category: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Get memory entries, optionally filtered by category."""
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    """SELECT * FROM memory
                       WHERE category = ?
                       ORDER BY importance DESC, created_at DESC
                       LIMIT ?""",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM memory
                       ORDER BY importance DESC, created_at DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_memories(self, days: int = 7) -> list[dict]:
        """Get recent memories (for self-review)."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM memory
                   WHERE created_at > datetime('now', ?)
                   ORDER BY importance DESC, created_at DESC""",
                (f"-{days} days",),
            ).fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Portfolio snapshots
    # -------------------------------------------------------------------------

    def record_portfolio_snapshot(self, total_value_usd: float, cash_usd: float, positions: list):
        """Record a portfolio snapshot (called periodically)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO portfolio_snapshots (total_value_usd, cash_usd, positions_json)
                   VALUES (?, ?, ?)""",
                (total_value_usd, cash_usd, json.dumps(positions)),
            )
            conn.commit()

    def get_portfolio_history(self, days: int = 30) -> list[dict]:
        """Get portfolio history."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM portfolio_snapshots
                   WHERE created_at > datetime('now', ?)
                   ORDER BY created_at ASC""",
                (f"-{days} days",),
            ).fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
                (key, value, value),
            )
            conn.commit()
