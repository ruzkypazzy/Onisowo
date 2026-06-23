#!/usr/bin/env python3
"""Update TRADE_LOG.md from the live journal.

Run this after every trade (or on a cron) to keep TRADE_LOG.md
in sync with what's actually in the running bot's database.

Usage:
    python3 scripts/update_trade_log.py                  # writes to TRADE_LOG.md
    python3 scripts/update_trade_log.py --stdout         # prints to stdout
    python3 scripts/update_trade_log.py --db /path/to.db # custom db path

The output is the same format as the static TRADE_LOG.md but with
all the latest trades pulled from the SQLite journal.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime


def fetch_trades(db_path: str) -> list:
    """Fetch all trades from the journal, newest first."""
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, symbol, side, order_type, size, price AS entry_price,
                   quote_usd, order_id, opened_at, closed_at, status,
                   pnl_usd, pnl_pct, reason, skills_used, confidence,
                   tp_pct, sl_pct
            FROM trades
            ORDER BY id DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def render_trade_log(trades: list) -> str:
    """Render the trade log in markdown."""
    if not trades:
        return """# Live Trading Log — Àkànjí Oníṣòwò

> Live trading record for the Bitget AI Base Camp Hackathon S1 submission.
> All trades executed on Bitget (UTA), driven by Qwen 3.6 Plus.
> Last updated: *empty — no trades yet. Run `/pick` on the bot to start.*

## Summary

| Metric | Value |
|---|---:|
| Total trades | 0 |
| Win rate | n/a |
| Total P&L | $0.00 |

## Trades

_No trades yet._
"""

    open_trades = [t for t in trades if t.get("status") == "open"]
    closed_trades = [t for t in trades if t.get("status") == "closed"]
    wins = sum(1 for t in closed_trades if (t.get("pnl_usd") or 0) > 0)
    losses = sum(1 for t in closed_trades if (t.get("pnl_usd") or 0) < 0)
    be = sum(1 for t in closed_trades if (t.get("pnl_usd") or 0) == 0)
    total_pnl = sum((t.get("pnl_usd") or 0) for t in closed_trades)
    total_volume = sum((t.get("quote_usd") or 0) for t in trades)
    win_rate = (wins / len(closed_trades) * 100) if closed_trades else 0

    if trades:
        first = trades[-1]
        last = trades[0]
        period = f"{first['opened_at'][:10]} to {last['opened_at'][:10]}"
    else:
        period = "n/a"

    lines = [
        "# Live Trading Log — Àkànjí Oníṣòwò",
        "",
        "> Live trading record for the Bitget AI Base Camp Hackathon S1 submission.",
        "> All trades executed on Bitget (UTA), driven by Qwen 3.6 Plus decisions.",
        f"> Last updated: `{datetime.utcnow().isoformat(timespec='seconds')}Z`",
        f"> Period: *{period}*",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total trades | {len(trades)} ({len(closed_trades)} closed, {len(open_trades)} open) |",
        f"| Win rate | {win_rate:.0f}% ({wins}W / {losses}L / {be}BE) |",
        f"| Total P&L | ${total_pnl:+.4f} |",
        f"| Total volume | ${total_volume:.2f} |",
        f"| Avg trade size | ${(total_volume / len(trades)):.2f} |" if trades else "| Avg trade size | n/a |",
        f"| Broker | Bitget (UTA, V3 endpoints) |",
        f"| Decision engine | Qwen 3.6 Plus |",
        "",
        "## Trades (newest first)",
        "",
        "| # | Opened (UTC) | Symbol | Side | Entry | Notional | TP | SL | Order ID | Status | P&L |",
        "|---:|---|---|---|---:|---:|---:|---:|---|---|---:|",
    ]

    for t in trades:
        tp = f"+{t.get('tp_pct', 0):.1f}%" if t.get('tp_pct') else "—"
        sl = f"{t.get('sl_pct', 0):.1f}%" if t.get('sl_pct') else "—"
        pnl_str = f"${t.get('pnl_usd', 0) or 0:+.4f} ({t.get('pnl_pct', 0) or 0:+.2f}%)" if t.get("status") == "closed" else "—"
        side_emoji = "🟢" if t.get("side") == "buy" else "🔴"
        lines.append(
            f"| {t['id']} | {t['opened_at'][:19]} | {t['symbol']} | "
            f"{side_emoji} {t['side'].upper()} | "
            f"${t.get('entry_price', 0) or 0:.4f} | ${t.get('quote_usd', 0) or 0:.2f} | "
            f"{tp} | {sl} | `{t.get('order_id', '—') or '—'}` | "
            f"{t.get('status', '—')} | {pnl_str} |"
        )

    lines.extend([
        "",
        "## How to verify",
        "",
        "Every `Order ID` above is a real order on the Bitget order book. ",
        "Sign in to [bitget.com](https://www.bitget.com) with the same UID (7781181263) to verify.",
        "",
        "## How this log is generated",
        "",
        "This file is auto-generated from the bot's SQLite journal by `scripts/update_trade_log.py`.",
        "Run it after each trade to keep it in sync:",
        "",
        "```bash",
        "cd /opt/akanji",
        "python3 scripts/update_trade_log.py",
        "git add TRADE_LOG.md && git commit -m 'chore: refresh trade log'",
        "```",
        "",
        f"_Generated {datetime.utcnow().isoformat(timespec='seconds')}Z by `update_trade_log.py`._",
    ])

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Update TRADE_LOG.md from the live journal")
    parser.add_argument(
        "--db",
        default=os.environ.get("DATABASE_PATH", "./db/onisowo.db"),
        help="Path to the SQLite journal (default: $DATABASE_PATH or ./db/onisowo.db)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of writing to TRADE_LOG.md",
    )
    args = parser.parse_args()

    trades = fetch_trades(args.db)
    rendered = render_trade_log(trades)

    if args.stdout:
        print(rendered)
    else:
        out_path = "TRADE_LOG.md"
        with open(out_path, "w") as f:
            f.write(rendered)
        n_open = sum(1 for t in trades if t.get("status") == "open")
        n_closed = sum(1 for t in trades if t.get("status") == "closed")
        print(f"✓ Wrote {out_path} ({n_open} open + {n_closed} closed trades)")


if __name__ == "__main__":
    main()
