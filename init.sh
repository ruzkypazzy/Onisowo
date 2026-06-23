#!/bin/bash
# Àkànjí Oníṣòwò — one-command self-hostable installer
# Usage:
#   bash init.sh                                    (interactive)
#   bash init.sh --non-interactive                   (uses placeholders, edit .env later)
#   bash init.sh --platform=telegram|app|both        (default: both)
#   curl -fsSL .../init.sh | bash                    (remote one-liner)
#
# What it does:
#   1. Asks: Telegram, Mobile app (PWA), or both
#   2. Clones the repo (assets + PWA bundled)
#   3. Creates Python venv, installs deps
#   4. Sets up systemd service for the chosen platform(s)
#   5. Installs brand assets (background, profile picture, brand mark)
#   6. Auto-restarts on crash, auto-starts on VPS reboot
#
# To control the bot after install:
#   sudo systemctl status akanji              # is it running?
#   sudo systemctl restart akanji              # restart it
#   sudo journalctl -u akanji -f               # watch the logs live
#   sudo systemctl stop akanji                 # stop the bot
#   /opt/akanji/uninstall.sh                   # remove the service + files

set -e

NON_INTERACTIVE=0
PLATFORM="both"
if [ "$1" = "--non-interactive" ] || [ "$1" = "-y" ]; then
    NON_INTERACTIVE=1
fi
if [ "${1#--platform=}" != "$1" ]; then
    PLATFORM="${1#--platform=}"
fi
if [ "${2#--platform=}" != "$2" ]; then
    PLATFORM="${2#--platform=}"
fi

SERVICE_NAME="akanji"
REPO_URL="https://github.com/ruzkypazzy/Akanji-Onisowo.git"
INSTALL_DIR="/opt/akanji"
DATA_DIR="/var/lib/akanji"

echo "============================================================"
echo "  Àkànjí Oníṣòwò — Self-hostable installer"
echo "  The Trader. Yoruba merchant wisdom + crypto."
echo "============================================================"
echo ""

# 0. Ask the user which platform they want
if [ $NON_INTERACTIVE -eq 0 ]; then
    echo "  Which platform(s) do you want to install?"
    echo "    1) Telegram bot  (chat via @OnisowoBot on your phone)"
    echo "    2) Mobile app    (PWA on your home screen, same backend)"
    echo "    3) Both          (Telegram + mobile app, share the same brain)"
    echo ""
    read -p "  Choose [1/2/3] (default 3): " PLATFORM_CHOICE
    case "$PLATFORM_CHOICE" in
        1) PLATFORM="telegram" ;;
        2) PLATFORM="app" ;;
        3|"") PLATFORM="both" ;;
        *) PLATFORM="both" ;;
    esac
fi

echo "  Install dir:  $INSTALL_DIR"
echo "  Service:      $SERVICE_NAME (systemd)"
echo "  Platform:     $PLATFORM"
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

# systemd
if ! command -v systemctl &> /dev/null; then
    echo "  ⚠️  systemd not found. The bot will need to be run manually with 'python main.py'."
    HAS_SYSTEMD=0
else
    HAS_SYSTEMD=1
    echo "  ✓ systemd available"
fi

# 2. Clone or update the repo (this includes the assets + PWA bundle)
echo ""
echo "→ Installing source code + brand assets..."
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

if [ -f "app/index.html" ] && [ -f "app/manifest.json" ]; then
    echo "  ✓ Mobile PWA bundle present"
else
    echo "  ⚠️  Mobile PWA bundle missing. Run 'git pull' to refresh."
fi

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

# 4. Data directory
echo ""
echo "→ Setting up data directory..."
mkdir -p "$DATA_DIR/db" "$DATA_DIR/logs"
chown -R "$SUDO_USER:$SUDO_USER" "$DATA_DIR" 2>/dev/null || true
echo "  ✓ $DATA_DIR ready"

# 5. Link db + logs
if [ ! -L "db/onisowo.db" ] && [ ! -f "db/onisowo.db" ]; then
    [ -d db ] && rmdir db 2>/dev/null || true
    ln -s "$DATA_DIR/db" db
    echo "  ✓ Linked db/ → $DATA_DIR/db"
fi
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
    echo "  ⚠️  Non-interactive mode: .env has placeholders. Edit before starting."
    exit 0
fi

# 7. Prompt for required env values
echo ""
echo "→ Fill in your environment values:"
echo "  (Press Enter to keep current value or skip if you'll edit .env later)"
echo ""

prompt_env() {
    local var=$1
    local label=$2
    local current=$(grep "^$var=" .env 2>/dev/null | head -1 | cut -d= -f2-)
    local current_display="${current:0:25}***"
    [ -z "$current" ] && current_display="(empty)"
    read -p "  $label [$current_display]: " new_value
    if [ -n "$new_value" ]; then
        if grep -q "^$var=" .env; then
            sed -i "s|^$var=.*|$var=$new_value|" .env
        else
            echo "$var=$new_value" >> .env
        fi
    fi
}

if [ "$PLATFORM" = "telegram" ] || [ "$PLATFORM" = "both" ]; then
    echo "  Telegram bot setup (from @BotFather):"
    prompt_env "TELEGRAM_BOT_TOKEN"   "  Telegram bot token"
fi

echo ""
echo "  Bitget API (from bitget.com → API Management):"
prompt_env "BITGET_API_KEY"       "  Bitget API key"
prompt_env "BITGET_SECRET_KEY"    "  Bitget secret key"
prompt_env "BITGET_PASSPHRASE"    "  Bitget passphrase"

echo ""
echo "  Qwen / LLM API key (Bitget hackathon or your own):"
prompt_env "BITGET_QWEN_API_KEY"  "  Qwen API key"

echo ""
echo "  Owner gate (locks the bot to your Telegram user ID):"
echo "    Without this, anyone who finds @OnisowoBot can trade on your account."
echo "    To find your ID: message @userinfobot on Telegram."
prompt_env "OWNER_TELEGRAM_ID"   "  Your Telegram user ID (press Enter to skip)"

chmod 600 .env
echo "  ✓ .env saved (chmod 600)"

# 8. Install systemd service(s)
if [ $HAS_SYSTEMD -eq 1 ]; then
    echo ""
    echo "→ Installing systemd service(s)..."

    # Build the command list based on platform
    EXEC_CMDS=""
    if [ "$PLATFORM" = "telegram" ] || [ "$PLATFORM" = "both" ]; then
        EXEC_CMDS="${EXEC_CMDS}ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/main.py\n"
    fi
    if [ "$PLATFORM" = "app" ] || [ "$PLATFORM" = "both" ]; then
        if [ -n "$EXEC_CMDS" ]; then
            EXEC_CMDS="${EXEC_CMDS}ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/mobile_server.py\n"
        else
            EXEC_CMDS="ExecStart=$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/mobile_server.py\n"
        fi
    fi

    # For 'both' mode, run mobile_server first (in background), then main.py
    # Simpler: run them as two services. For now, single service that runs both via wrapper.
    if [ "$PLATFORM" = "both" ]; then
        # Use a wrapper script that runs both
        cat > /usr/local/bin/akanji-run.sh <<'WRAPPER'
#!/bin/bash
# Run both Telegram bot and mobile server. Mobile server is the parent
# process for the systemd unit; Telegram bot runs as a background child.
set -e
INSTALL_DIR="/opt/akanji"
cd "$INSTALL_DIR"
source .venv/bin/activate
# Start Telegram bot in the background
python main.py &
TELEGRAM_PID=$!
# Trap signals to clean up the child
trap "kill $TELEGRAM_PID 2>/dev/null; exit 0" SIGTERM SIGINT
# Start mobile server in the foreground (systemd tracks this PID)
python mobile_server.py
WRAPPER
        chmod +x /usr/local/bin/akanji-run.sh
        EXEC_START="/usr/local/bin/akanji-run.sh"
    elif [ "$PLATFORM" = "telegram" ]; then
        EXEC_START="$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/main.py"
    else
        EXEC_START="$INSTALL_DIR/.venv/bin/python $INSTALL_DIR/mobile_server.py"
    fi

    cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=Àkànjí Oníṣòwò — AI Trading Agent (${PLATFORM})
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=${EXEC_START}
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
    sleep 3

    if systemctl is-active --quiet $SERVICE_NAME; then
        echo "  ✓ Service started and enabled (auto-starts on reboot)"
    else
        echo "  ⚠️  Service didn't start. Check logs:"
        echo "     sudo journalctl -u $SERVICE_NAME -n 50"
    fi
else
    echo "  ⚠️  No systemd. Start the bot manually:"
    if [ "$PLATFORM" = "telegram" ]; then
        echo "     cd $INSTALL_DIR && source .venv/bin/activate && python main.py"
    elif [ "$PLATFORM" = "app" ]; then
        echo "     cd $INSTALL_DIR && source .venv/bin/activate && python mobile_server.py"
    else
        echo "     cd $INSTALL_DIR && source .venv/bin/activate && python main.py &"
        echo "     python mobile_server.py"
    fi
fi

# 9. Verify
echo ""
echo "→ Verifying..."
sleep 2
if [ $HAS_SYSTEMD -eq 1 ] && systemctl is-active --quiet $SERVICE_NAME; then
    echo "  ✓ Service is RUNNING (PID $(systemctl show $SERVICE_NAME -p MainPID --value))"
    if [ "$PLATFORM" = "app" ] || [ "$PLATFORM" = "both" ]; then
        sleep 1
        if curl -sf http://localhost:8765/health > /dev/null 2>&1; then
            echo "  ✓ Mobile API is responding at http://localhost:8765/health"
        else
            echo "  ⚠️  Mobile API not responding yet. Wait a few seconds, then check:"
            echo "     curl http://localhost:8765/health"
        fi
    fi
else
    echo "  ⚠️  Service not running. Check:"
    echo "     sudo journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi

# 11. Summary
echo ""
echo "============================================================"
echo "  ✓ Installation complete!"
echo "============================================================"
echo ""
echo "  Manage the bot:"
echo "     sudo systemctl status $SERVICE_NAME"
echo "     sudo systemctl restart $SERVICE_NAME"
echo "     sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "  Update the bot (after pulling new code from GitHub):"
echo "     cd $INSTALL_DIR && git pull && sudo systemctl restart $SERVICE_NAME"
echo ""
echo "  Uninstall:"
echo "     bash $INSTALL_DIR/uninstall.sh"
echo ""
if [ "$PLATFORM" = "telegram" ] || [ "$PLATFORM" = "both" ]; then
    echo "  Telegram: open the bot and send /start to greet Àkànjí."
fi
if [ "$PLATFORM" = "app" ] || [ "$PLATFORM" = "both" ]; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "  Mobile app: open http://$IP:8765/ on your phone."
    echo "    (Use Safari on iPhone or Chrome on Android → 'Add to Home Screen')"
fi
echo ""
echo "  Àkànjí, The Trader. Ọniṣọwọ́ ẹ káàlẹ́! 🛍️"
