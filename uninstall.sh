#!/bin/bash
# Àkànjí Oníṣòwò — uninstall
# Removes the systemd service and (optionally) all install files.
#
# Usage:
#   bash uninstall.sh                # stop + remove service, keep install files
#   bash uninstall.sh --purge        # also delete /opt/akanji and /var/lib/akanji
#   bash uninstall.sh --keep-data    # remove service + install, keep the DB

set -e

SERVICE_NAME="akanji"
INSTALL_DIR="/opt/akanji"
DATA_DIR="/var/lib/akanji"
PURGE=0
KEEP_DATA=0

if [ "$1" = "--purge" ]; then
    PURGE=1
elif [ "$1" = "--keep-data" ]; then
    KEEP_DATA=1
fi

if [ "$EUID" -ne 0 ]; then
    echo "❌ Run as root: sudo bash uninstall.sh"
    exit 1
fi

echo "============================================================"
echo "  Àkànjí Oníṣòwò — Uninstaller"
echo "============================================================"
echo ""

# Stop + disable service
if command -v systemctl &> /dev/null && systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
    echo "→ Stopping $SERVICE_NAME service..."
    systemctl stop $SERVICE_NAME 2>/dev/null || true
    systemctl disable $SERVICE_NAME 2>/dev/null || true
    rm -f /etc/systemd/system/${SERVICE_NAME}.service
    systemctl daemon-reload
    echo "  ✓ Service stopped and removed"
else
    echo "→ No systemd service found (already uninstalled?)"
fi

# Remove the wrapper script (used when both telegram + mobile are running)
if [ -f /usr/local/bin/akanji-run.sh ]; then
    rm -f /usr/local/bin/akanji-run.sh
    echo "  ✓ Removed /usr/local/bin/akanji-run.sh"
fi

# Remove install dir
if [ $PURGE -eq 1 ] && [ $KEEP_DATA -eq 0 ]; then
    echo "→ Removing $INSTALL_DIR (purge mode)…"
    rm -rf "$INSTALL_DIR"
    echo "  ✓ Install dir removed"
    if [ -d "$DATA_DIR" ]; then
        rm -rf "$DATA_DIR"
        echo "  ✓ Data dir removed"
    fi
elif [ $KEEP_DATA -eq 1 ]; then
    if [ -d "$INSTALL_DIR" ]; then
        echo "→ Removing $INSTALL_DIR (keeping data)…"
        rm -rf "$INSTALL_DIR"
        echo "  ✓ Install dir removed (your DB lives in $DATA_DIR — re-install to reuse it)"
    fi
else
    echo "→ Install dir kept at $INSTALL_DIR (use --purge to remove)"
fi

echo ""
echo "✓ Uninstalled."
echo ""
echo "To reinstall, run: bash <(curl -fsSL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/init.sh)"
