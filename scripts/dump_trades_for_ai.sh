#!/bin/bash
# Àkànjí Oníṣòwò — dump trades for the AI assistant to ingest
# Run this on the VPS, paste the output back to the assistant.
set -e
cd /opt/akanji
echo "=== TRADE LOG DUMP (paste this back to the assistant) ==="
echo ""
python3 scripts/update_trade_log.py
echo ""
echo "=== RAW SQL DUMP ==="
sqlite3 -header -column db/onisowo.db <<SQL
SELECT id, symbol, side, order_type, status, ROUND(quote_usd, 2) AS notional,
       ROUND(price, 4) AS entry, ROUND(pnl_usd, 4) AS pnl_usd, ROUND(pnl_pct, 2) AS pnl_pct,
       order_id, opened_at, closed_at, tp_pct, sl_pct
FROM trades
ORDER BY id;
SQL
