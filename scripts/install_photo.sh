#!/usr/bin/env bash
# One-shot: pull latest Oniṣòwò + restart systemd service.
# Run on VPS. Idempotent — safe to run multiple times.
set -e

# Find the repo. Canonical location is /opt/akanji; fall back to common alternatives.
if [ -d /opt/akanji/.git ]; then
  cd /opt/akanji
elif [ -d "$(dirname "$0")/.." ] && [ -d "$(dirname "$0")/../.git" ]; then
  cd "$(dirname "$0")/.."
elif [ -d ~/akanji/.git ]; then
  cd ~/akanji
else
  echo "❌ Can't find the akanji repo. Tried: /opt/akanji, ~/akanji, script-relative."
  echo "Run this from inside the repo, or set up the repo at /opt/akanji first:"
  echo "  sudo git clone https://github.com/ruzkypazzy/Akanji-Onisowo.git /opt/akanji"
  exit 1
fi
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
