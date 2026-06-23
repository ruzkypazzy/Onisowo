#!/bin/bash
# Àkànjí Oníṣòwò — completely fresh restart
# This kills ALL Python processes, removes bytecode, pulls, restarts.

set -e
cd /opt/akanji 2>/dev/null || { echo "❌ /opt/akanji not found"; exit 1; }

echo "═══════════════════════════════════════════════════════════"
echo "  Àkànjí Oníṣòwò — force fresh restart"
echo "═══════════════════════════════════════════════════════════"

# 1. Fix git remote (in case it's still pointing at the old repo)
git remote set-url origin https://github.com/ruzkypazzy/Akanji-Onisowo.git 2>/dev/null

# 2. Force pull latest code (this is destructive to local changes)
git fetch origin main
git reset --hard origin/main
HEAD=$(git rev-parse --short HEAD)
echo "✓ Now on commit $HEAD"

# 3. Kill ALL Python processes that might be running the bot
echo ""
echo "→ Killing all akanji bot processes..."
pkill -9 -f "main.py" 2>/dev/null || true
pkill -9 -f "tgbot.bot" 2>/dev/null || true
pkill -9 -f "akanji" 2>/dev/null || true
sleep 3
ps auxf | grep -E "main\.py|akanji" | grep -v grep || echo "✓ No bot processes running"

# 4. Clear ALL bytecode cache everywhere
echo ""
echo "→ Clearing bytecode cache..."
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find /var/lib/akanji -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
echo "✓ cleared"

# 5. Stop systemd
echo ""
echo "→ Stopping systemd service..."
sudo systemctl stop akanji 2>/dev/null || true
sleep 2

# 6. Start systemd fresh
echo "→ Starting systemd service fresh..."
sudo systemctl start akanji
sleep 3
sudo systemctl is-active --quiet akanji && echo "✓ akanji is active" || {
    echo "❌ akanji failed to start. Log:"
    sudo journalctl -u akanji -n 20 --no-pager
    exit 1
}

# 7. Show the last 5 log lines
echo ""
echo "→ Last 5 log lines:"
sudo journalctl -u akanji -n 5 --no-pager

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✅ Force fresh restart complete. Send /start in Telegram."
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "You should see ONLY the long help text, no bottom keyboard chips."
