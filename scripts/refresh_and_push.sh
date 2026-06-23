#!/bin/bash
# Àkànjí Oníṣòwò — refresh TRADE_LOG.md from live journal and push to git
# Run this on the VPS to keep TRADE_LOG.md in sync.
set -e
cd /opt/akanji

echo "→ Refreshing TRADE_LOG.md from live journal..."
python3 scripts/update_trade_log.py

echo ""
echo "→ Showing diff..."
git diff --stat TRADE_LOG.md 2>/dev/null || echo "  (no git changes)"

echo ""
echo "→ Commit + push to GitHub..."
git add TRADE_LOG.md
if git diff --cached --quiet TRADE_LOG.md; then
    echo "  No changes to commit."
else
    git commit -m "chore: refresh trade log ($(date +%Y-%m-%dT%H:%M:%SZ))"
    git push origin main
    echo "  ✓ Pushed to GitHub"
fi
