#!/bin/bash
# Oniṣòwò — one-command setup script
# Usage: bash init.sh

set -e

echo "============================================================"
echo "  Ọniṣọwọ́ (Oniṣòwò) — Self-hostable setup"
echo "============================================================"
echo ""

# 1. Check Python
echo "→ Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "  ❌ Python 3 not found. Install Python 3.10+ first."
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  ✓ Python $PY_VERSION found"

# 2. Create venv
echo ""
echo "→ Creating virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  ✓ Created .venv"
else
    echo "  ✓ .venv already exists"
fi

# 3. Activate
# shellcheck disable=SC1091
source .venv/bin/activate
echo "  ✓ Activated venv"

# 4. Install deps
echo ""
echo "→ Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "  ✓ Installed"

# 5. Create .env if missing
echo ""
if [ ! -f ".env" ]; then
    echo "→ Creating .env from .env.example..."
    cp .env.example .env
    chmod 600 .env
    echo "  ✓ Created .env (you MUST edit it now)"
    echo ""
    echo "  ┌─────────────────────────────────────────────────────────┐"
    echo "  │  ⚠️  YOU MUST EDIT .env BEFORE RUNNING                  │"
    echo "  │                                                         │"
    echo "  │  1. Get a Telegram bot token from @BotFather            │"
    echo "  │  2. Get a Bitget API key (Read+Trade, no Withdraw)      │"
    echo "  │  3. Get a Qwen API key (from Bitget hackathon email)    │"
    echo "  │  4. Paste all 5 into .env                               │"
    echo "  │                                                         │"
    echo "  │  Then run: python main.py                               │"
    echo "  └─────────────────────────────────────────────────────────┘"
    echo ""
    echo "Opening .env in nano..."
    sleep 2
    ${EDITOR:-nano} .env
else
    echo "→ .env already exists (not overwriting)"
    echo "  Edit it with: nano .env"
fi

# 6. Create db dir
echo ""
echo "→ Setting up database directory..."
mkdir -p db
echo "  ✓ Created db/"

# 7. Sanity check
echo ""
echo "→ Running sanity check..."
python3 -c "from clients.bitget import BitgetClient; from clients.qwen import QwenClient; print('  ✓ All modules import correctly')" || {
    echo "  ❌ Import failed. Check your Python environment."
    exit 1
}

echo ""
echo "============================================================"
echo "  ✓ Setup complete!"
echo "============================================================"
echo ""
echo "  Next steps:"
echo "    1. Make sure your .env is filled in (nano .env)"
echo "    2. Run: source .venv/bin/activate"
echo "    3. Run: python main.py"
echo ""
echo "  Once running, open Telegram and message your bot."
echo ""
echo "  Ọniṣọwọ́ káàlẹ́! 🛍️"
