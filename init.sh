#!/bin/bash
# Àkànjí Oníṣòwò — one-command self-hostable installer
# Usage:
#   bash init.sh                                  (interactive, opens nano to fill .env)
#   bash init.sh --non-interactive                 (uses placeholders, you edit .env later)
#   curl -fsSL .../init.sh | bash -s -- --non-interactive   (remote one-liner)
#
# What it does:
#   1. Clones the repo if not present, pulls latest if present
#   2. Creates Python venv, installs deps
#   3. Creates a systemd service that runs the bot in the background
#   4. Auto-restarts on crash, auto-starts on VPS reboot
#   5. No screen, no manual start, no babysitting
#
# To control the bot after install:
#   sudo systemctl status akanji   # is it running?
#   sudo systemctl restart akanji   # restart it
#   sudo journalctl -u akanji -f    # watch the logs live
#   sudo systemctl stop akanji      # stop the bot
#   bash uninstall.sh                # remove the service + files

set -e

NON_INTERACTIVE=0
if [ "$1" = "--non-interactive" ] || [ "$1" = "-y" ]; then
    NON_INTERACTIVE=1
fi

SERVICE_NAME="akanji"
REPO_URL="https://github.com/ruzkypazzy/Onisowo.git"
INSTALL_DIR="/opt/akanji"
DATA_DIR="/var/lib/akanji"

echo "============================================================"
echo "  Àkànjí Oníṣòwò — Self-hostable installer"
echo "============================================================"
echo ""
echo "  Install dir:  $INSTALL_DIR"
echo "  Service:      $SERVICE_NAME (systemd)"
echo "  Mode:         $([ $NON_INTERACTIVE -eq 1 ] && echo 'non-interactive' || echo 'interactive')"
echo ""

# 1. Check prerequisites
echo "→ Checking prerequisites..."

# Root?
if [ "$EUID" -ne 0 ]; then
    echo "  ❌ This installer needs to run as root (for systemd + /opt)."
    echo "     Re-run with: sudo bash init.sh"
    exit 1
fi

# Python 3.10+
if ! command -v python3 &> /dev/null; then
    echo "  ❌ Python 3 not found. Install with: apt install python3 python3-venv python3-pip"
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo $PY_VERSION | cut -d. -f1)
PY_MINOR=$(echo $PY_VERSION | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "  ❌ Python 3.10+ required (you have $PY_VERSION)"
    exit 1
fi
echo "  ✓ Python $PY_VERSION"

# Git
if ! command -v git &> /dev/null; then
    echo "  ❌ Git not found. Install with: apt install git"
    exit 1
fi
echo "  ✓ Git $(git --version | awk '{print $3}')"

# systemd (so we can run as a service)
if ! command -v systemctl &> /dev/null; then
    echo "  ⚠️  systemd not found. The bot will need to be run manually with 'python main.py'."
    echo "     (This usually means you're on a non-systemd system like Alpine.)"
    HAS_SYSTEMD=0
else
    HAS_SYSTEMD=1
    echo "  ✓ systemd available"
fi

# 2. Clone or update the repo
echo ""
echo "→ Installing source code..."
mkdir -p "$(dirname "$INSTALL_DIR")"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  → Updating existing install at $INSTALL_DIR"
    cd "$INSTALL_DIR"
    git pull --ff-only || {
        echo "  ⚠️  git pull failed (maybe local changes?). Continuing with existing code."
    }
else
    echo "  → Cloning $REPO_URL → $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
echo "  ✓ Source ready (commit $(git rev-parse --short HEAD))"

# 3. Python venv + dependencies
echo ""
echo "→ Setting up Python environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  ✓ Created .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "  ✓ Dependencies installed"

# 4. Data directory (logs, db)
echo ""
echo "→ Setting up data directory..."
mkdir -p "$DATA_DIR/db" "$DATA_DIR/logs"
chown -R "$SUDO_USER:$SUDO_USER" "$DATA_DIR" 2>/dev/null || true
echo "  ✓ $DATA_DIR ready"

# 5. Link db/ to the data dir (so DB survives reinstalls)
if [ ! -L "db/onisowo.db" ] && [ ! -f "db/onisowo.db" ]; then
    # Move any local db out of the way
    [ -d db ] && rmdir db 2>/dev/null || true
    ln -s "$DATA_DIR/db" db
    echo "  ✓ Linked db/ → $DATA_DIR/db"
fi
# Symlink logs to /var/log so journalctl captures them
if [ ! -L "logs" ]; then
    [ -d logs ] && rmdir logs 2>/dev/null || true
    ln -s "$DATA_DIR/logs" logs
    echo "  ✓ Linked logs/ → $DATA_DIR/logs"
fi

# 6. .env file
echo ""
echo "→ Setting up environment config (.env)..."
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo "  ✓ Created .env (chmod 600 — read-only for owner)"
fi

if [ $NON_INTERACTIVE -eq 1 ]; then
    echo "  ⚠️  Non-interactive mode: .env has placeholders. Edit before starting:"
    echo "     sudo nano $INSTALL_DIR/.env"
    echo ""
    echo "After editing, enable the service with:"
    echo "     sudo systemctl enable --now $SERVICE_NAME"
    echo ""
    echo "  Then watch the logs:"
    echo "     sudo journalctl -u $SERVICE_NAME -f"
    exit 0
fi

# 7. Prompt for the 5 required values
echo ""
echo "→ Fill in your 5 environment values:"
echo "  (Press Enter to keep the current value or skip if you'll edit .env later)"
echo ""

prompt_env() {
    local var=$1
    local current=$(grep "^$var=" .env 2>/dev/null | head -1 | cut -d= -f2-)
    local label=$2
    local current_display="${current:0:25}***"
    [ -z "$current" ] && current_display="(empty)"
    read -p "  $label [$current_display]: " new_value
    if [ -n "$new_value" ]; then
        # Replace or append
        if grep -q "^$var=" .env; then
            sed -i "s|^$var=.*|$var=$new_value|" .env
        else
            echo "$var=$new_value" >> .env
        fi
    fi
}

prompt_env "TELEGRAM_BOT_TOKEN"   "Telegram bot token (from @BotFather)"
prompt_env "BITGET_API_KEY"       "Bitget API key"
prompt_env "BITGET_SECRET_KEY"    "Bitget secret key"
prompt_env "BITGET_PASSPHRASE"    "Bitget passphrase"
prompt_env "BITGET_QWEN_API_KEY"  "Qwen / OpenAI API key"

chmod 600 .env
echo "  ✓ .env saved (chmod 600)"

# 8. systemd service
if [ $HAS_SYSTEMD -eq 1 ]; then
    echo ""
    echo "→ Installing systemd service ($SERVICE_NAME)..."

    cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Àkànjí Oníṣòwò — Telegram AI Trading Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=append:$DATA_DIR/logs/stdout.log
StandardError=append:$DATA_DIR/logs/stderr.log
SyslogIdentifier=$SERVICE_NAME
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    systemctl start $SERVICE_NAME
    sleep 2

    if systemctl is-active --quiet $SERVICE_NAME; then
        echo "  ✓ Service started and enabled (auto-starts on reboot)"
    else
        echo "  ⚠️  Service didn't start. Check logs:"
        echo "     sudo journalctl -u $SERVICE_NAME -n 50"
    fi
else
    echo "  ⚠️  No systemd. Start the bot manually:"
    echo "     cd $INSTALL_DIR && source .venv/bin/activate && python main.py"
fi

# 9. Verify
echo ""
echo "→ Verifying..."
sleep 3
if [ $HAS_SYSTEMD -eq 1 ] && systemctl is-active --quiet $SERVICE_NAME; then
    echo "  ✓ Bot is RUNNING (PID $(systemctl show $SERVICE_NAME -p MainPID --value))"
else
    echo "  ⚠️  Bot not running. Check:"
    echo "     sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi

# 10. Summary
echo ""
echo "============================================================"
echo "  ✓ Installation complete!"
echo "============================================================"
echo ""
echo "  Manage the bot:"
echo "     sudo systemctl status $SERVICE_NAME    # is it running?"
echo "     sudo systemctl restart $SERVICE_NAME    # restart it"
echo "     sudo systemctl stop $SERVICE_NAME       # stop it"
echo "     sudo journalctl -u $SERVICE_NAME -f     # watch logs live"
echo ""
echo "  Update the bot:"
echo "     cd $INSTALL_DIR"
echo "     git pull"
echo "     sudo systemctl restart $SERVICE_NAME"
echo ""
echo "  Uninstall:"
echo "     bash $INSTALL_DIR/uninstall.sh"
echo ""
echo "  Next step: open Telegram and message your bot."
echo "  It should reply with the Yoruba greeting."
echo ""
echo "  Àkànjí, The Trader. Ọnṣọ̀wọ́ ẹ káàlẹ́! 🛍️"
