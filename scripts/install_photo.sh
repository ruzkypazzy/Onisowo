#!/usr/bin/env bash
# One-shot: pull latest Oniṣòwò + restart systemd service.
# Run on VPS. Idempotent — safe to run multiple times.
set -e

cd "$(dirname "$0")/.." || cd ~/onisowo || cd /opt/akanji || cd /root/onisowo
echo "→ Working dir: $(pwd)"

if [ ! -d .git ]; then
  echo "❌ Not in a git repo. Run this from inside the onisowo repo dir."
  exit 1
fi

echo "→ Pulling latest from main..."
git fetch origin
git reset --hard origin/main

echo "→ Restarting systemd service..."
SERVICE=$(systemctl list-units --type=service --no-legend | grep -i akanji | awk '{print $1}' | head -1)
if [ -z "$SERVICE" ]; then
  SERVICE=akanji.service
fi
echo "  Service: $SERVICE"
sudo systemctl restart "$SERVICE"
sleep 2
echo ""
echo "→ Service status:"
sudo systemctl status "$SERVICE" --no-pager -l | head -10
echo ""
echo "→ Recent logs (last 20 lines):"
sudo journalctl -u "$SERVICE" -n 20 --no-pager
echo ""
echo "✓ Done."
echo ""
echo "Next: open Telegram, find @OnisowoBot, send the photo as a message."
