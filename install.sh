#!/bin/bash
# Àkànjí Oníṣòwò — one-line installer
#
# Default: install to /opt/akanji as a systemd service (Contabo / VPS / server)
#   curl one-liner:
#     bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh)
#
# No-root: install to ~/akanji, run as a foreground process (Mac / Linux laptop / any VPS)
#   bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh) --user
#
# Custom install dir:
#   bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh) --dir=$HOME/myakanji
#
# What this does:
#   1. Detects whether you have root (for /opt/akanji + systemd) or are a regular user
#   2. Clones the repo to the install dir
#   3. Creates a Python venv
#   4. Installs all dependencies
#   5. Prompts for 6 env vars (5 required + 1 recommended)
#   6. Either installs the systemd service (if root) OR shows you how to run it manually

set -e

# -------- Parse args --------
INSTALL_DIR="/opt/akanji"   # default for root installs
USE_SYSTEMD=auto              # auto-detect based on EUID
REPO_URL="https://github.com/ruzkypazzy/Akanji-Onisowo.git"

for arg in "$@"; do
    case "$arg" in
        --user)
            INSTALL_DIR="$HOME/akanji"
            USE_SYSTEMD=no
            ;;
        --dir=*)
            INSTALL_DIR="${arg#--dir=}"
            USE_SYSTEMD=no
            ;;
        --no-systemd)
            USE_SYSTEMD=no
            ;;
        --systemd)
            USE_SYSTEMD=yes
            ;;
        --help|-h)
            cat <<HELP
Àkànjí Oníṣòwò installer

Usage:
  bash install.sh                  # install to /opt/akanji (needs root or sudo)
  bash install.sh --user           # install to ~/akanji (no root needed)
  bash install.sh --dir=PATH       # install to custom PATH (no root needed)
  bash install.sh --no-systemd     # install to default dir, skip systemd
  bash install.sh --systemd        # force systemd even if not root (will fail)

After install:
  • If you got a systemd service: sudo systemctl status akanji
  • If you're running as a regular user: cd $INSTALL_DIR && bash run.sh
HELP
            exit 0
            ;;
    esac
done

# Auto-detect: if running as root, default to systemd. Otherwise skip it.
if [ "$USE_SYSTEMD" = "auto" ]; then
    if [ "$EUID" -eq 0 ]; then
        USE_SYSTEMD=yes
    else
        USE_SYSTEMD=no
    fi
fi

echo "═══════════════════════════════════════════════════════════"
echo "  Àkànjí Oníṣòwò — AI Trading Agent for Bitget"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Install dir:  $INSTALL_DIR"
echo "  systemd:      $USE_SYSTEMD (auto-detected from EUID=$EUID)"
echo ""

# 1. Check Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Python 3 is required. Install it first:"
    echo "   macOS:   brew install python3"
    echo "   Ubuntu:  sudo apt install python3 python3-venv python3-pip"
    echo "   Fedora:  sudo dnf install python3 python3-pip"
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "✓ Python $PY_VERSION detected"

# 2. Check git
if ! command -v git >/dev/null 2>&1; then
    echo "❌ git is required. Install it first:"
    echo "   macOS:   brew install git"
    echo "   Ubuntu:  sudo apt install git"
    exit 1
fi
echo "✓ git detected"

# 3. Clone the repo
mkdir -p "$(dirname "$INSTALL_DIR")"
if [ -d "$INSTALL_DIR" ]; then
    echo "⚠ $INSTALL_DIR already exists. Pulling latest..."
    cd "$INSTALL_DIR" && git pull origin main
else
    echo "→ Cloning repo to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 4. Create venv
if [ ! -d ".venv" ]; then
    echo "→ Creating Python venv..."
    python3 -m venv .venv
fi
echo "→ Installing dependencies (this can take a minute)..."
.venv/bin/pip install --upgrade pip > /dev/null
.venv/bin/pip install -r requirements.txt

# 5. Generate .env (only if not present)
if [ ! -f ".env" ]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  Configuration"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    echo "I need 5 required values + 1 recommended. Press Enter to keep the default."
    echo ""

    read -p "TELEGRAM_BOT_TOKEN (from @BotFather): " TGBOT_TOKEN
    read -p "BITGET_API_KEY (Read+Trade, NO Withdraw): " BG_KEY
    read -p "BITGET_SECRET_KEY: " BG_SECRET
    read -p "BITGET_PASSPHRASE: " BG_PASS
    read -p "BITGET_QWEN_API_KEY (Qwen 3.6 Plus): " QWEN_KEY
    echo ""
    echo "Optional: lock the bot to your Telegram user ID (recommended)."
    echo "  • Without this, anyone who finds the bot can trade on your account."
    echo "  • To find your ID: message @userinfobot on Telegram."
    read -p "OWNER_TELEGRAM_ID (press Enter to skip): " OWNER_ID

    cat > .env <<EOF
# Àkànjí Oníṣòwò — generated $(date)
TELEGRAM_BOT_TOKEN=${TGBOT_TOKEN}
BITGET_API_KEY=${BG_KEY}
BITGET_SECRET_KEY=${BG_SECRET}
BITGET_PASSPHRASE=${BG_PASS}
BITGET_QWEN_API_KEY=${QWEN_KEY}
OWNER_TELEGRAM_ID=${OWNER_ID}
EOF
    chmod 600 .env
    echo "✓ .env created (chmod 600)"
fi

# 6. systemd service (only if root or asked)
SERVICE_FILE="/etc/systemd/system/akanji.service"
if [ "$USE_SYSTEMD" = "yes" ]; then
    if [ ! -f "$SERVICE_FILE" ]; then
        echo "→ Installing systemd service 'akanji'..."
        cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Àkànjí Oníṣòwò — AI Trading Agent
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
        systemctl daemon-reload
        systemctl enable akanji
        systemctl start akanji
        echo "✓ systemd service 'akanji' started"
    else
        echo "✓ systemd service 'akanji' already installed"
    fi
else
    echo ""
    echo "Skipping systemd (you're not root, or you passed --no-systemd)."
    echo "To run the bot manually, use:"
    echo ""
    echo "  cd $INSTALL_DIR"
    echo "  bash run.sh"
    echo ""
    echo "Or run directly:"
    echo "  cd $INSTALL_DIR && .venv/bin/python main.py"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✓ Install complete"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Install dir:  $INSTALL_DIR"
if [ "$USE_SYSTEMD" = "yes" ]; then
    echo "  Service:      systemctl status akanji"
    echo "  Logs:         journalctl -u akanji -f"
else
    echo "  Run:          cd $INSTALL_DIR && bash run.sh"
    echo "  Logs:         tail -f $INSTALL_DIR/logs/akanji.log (if started via run.sh)"
fi
echo "  Update later: cd $INSTALL_DIR && bash force-fresh.sh"
echo ""
echo "  Telegram:     message @OnisowoBot, send /start"
echo "  Source:       https://github.com/ruzkypazzy/Akanji-Onisowo"
echo ""
