#!/bin/bash
# Àkànjí Oníṣòwò — one-line installer
# Run with: bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh)

set -e
INSTALL_DIR="/opt/akanji"
REPO_URL="https://github.com/ruzkypazzy/Akanji-Onisowo.git"

echo "═══════════════════════════════════════════════════════════"
echo "  Àkànjí Oníṣòwò — Yoruba AI Trading Agent for Bitget"
echo "═══════════════════════════════════════════════════════════"
echo ""

# 1. Check Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 is required. Install it first."
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "✓ Python $PY_VERSION detected"

# 2. Check git
if ! command -v git >/dev/null 2>&1; then
    echo "❌ git is required. Install it first."
    exit 1
fi
echo "✓ git detected"

# 3. Clone the repo
if [ -d "$INSTALL_DIR" ]; then
    echo "⚠ $INSTALL_DIR already exists. Pulling latest..."
    cd "$INSTALL_DIR" && git pull origin main
else
    echo "→ Cloning repo to $INSTALL_DIR..."
    sudo git clone "$REPO_URL" "$INSTALL_DIR" || git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 4. Create venv
if [ ! -d ".venv" ]; then
    echo "→ Creating Python venv..."
    python3 -m venv .venv
fi
echo "→ Installing dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 5. Generate .env
if [ ! -f ".env" ]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  Configuration"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    echo "I need 5 values. Press Enter to keep the default (shown in brackets)."
    echo ""

    read -p "TELEGRAM_BOT_TOKEN (from @BotFather): " TGBOT_TOKEN
    read -p "BITGET_API_KEY (Read+Trade, NO Withdraw): " BG_KEY
    read -p "BITGET_SECRET_KEY: " BG_SECRET
    read -p "BITGET_PASSPHRASE: " BG_PASS
    read -p "BITGET_QWEN_API_KEY (Qwen 3.6 Plus): " QWEN_KEY

    cat > .env <<EOF
# Àkànjí Oníṣòwò — generated $(date)
TELEGRAM_BOT_TOKEN=${TGBOT_TOKEN}
BITGET_API_KEY=${BG_KEY}
BITGET_SECRET_KEY=${BG_SECRET}
BITGET_PASSPHRASE=${BG_PASS}
BITGET_QWEN_API_KEY=${QWEN_KEY}
EOF
    echo "✓ .env created"
fi

# 6. systemd service
SERVICE_FILE="/etc/systemd/system/akanji.service"
if [ ! -f "$SERVICE_FILE" ]; then
    echo "→ Installing systemd service 'akanji'..."
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Àkànjí Oníṣòwò — Yoruba AI Trading Agent
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python main.py
Restart=always
RestartSec=10
EnvironmentFile=$INSTALL_DIR/.env

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now akanji
    echo "✓ systemd service 'akanji' installed and started"
else
    echo "✓ systemd service 'akanji' already exists. Restarting..."
    sudo systemctl restart akanji
fi

# 7. Done
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✅ Àkànjí is running"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "Manage with:"
echo "  sudo systemctl status akanji    # status"
echo "  sudo systemctl restart akanji   # restart"
echo "  sudo journalctl -u akanji -f     # live logs"
echo ""
echo "Open Telegram and message your bot to start trading."
echo "Try /start, /pick, or /schedule daily 9am"
echo ""
echo "Oníṣòwò káàlẹ́ 🪶"
