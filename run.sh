#!/bin/bash
# Àkànjí Oníṣòwò — manual run script
#
# Use this if you installed with --user or --no-systemd and don't have
# a systemd service. The bot runs in the foreground, logs go to stdout
# and to logs/akanji.log. Press Ctrl+C to stop.
#
# Usage:
#   bash run.sh                 # run in foreground
#   bash run.sh --bg            # run in background, return to shell
#   bash run.sh --status        # check if background bot is running
#   bash run.sh --stop          # stop background bot
#   bash run.sh --logs          # tail the log file
#   bash run.sh --restart       # stop + start

set -e
cd "$(dirname "$0")"

# Check .env
if [ ! -f ".env" ]; then
    echo "❌ .env not found. Run install.sh first."
    exit 1
fi

# Check venv
if [ ! -d ".venv" ]; then
    echo "❌ .venv not found. Run install.sh first."
    exit 1
fi

# Background mode
PIDFILE="logs/akanji.pid"
LOGFILE="logs/akanji.log"
mkdir -p logs

case "${1:-fg}" in
    --bg|--background)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "⚠ Bot is already running (PID $(cat "$PIDFILE")). Use --status or --stop first."
            exit 1
        fi
        echo "→ Starting bot in background..."
        nohup .venv/bin/python main.py >> "$LOGFILE" 2>&1 &
        echo $! > "$PIDFILE"
        sleep 2
        if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "✓ Started (PID $(cat "$PIDFILE")). Log: $LOGFILE"
            echo "  Stop with: bash run.sh --stop"
            echo "  Tail with: bash run.sh --logs"
        else
            echo "❌ Bot failed to start. Check $LOGFILE"
            tail -20 "$LOGFILE"
            exit 1
        fi
        ;;
    --status)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            echo "✓ Running (PID $(cat "$PIDFILE"))"
        else
            echo "✗ Not running"
        fi
        ;;
    --stop)
        if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
            PID=$(cat "$PIDFILE")
            kill "$PID" 2>/dev/null || true
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null || true
            fi
            rm -f "$PIDFILE"
            echo "✓ Stopped"
        else
            echo "✗ Not running"
            rm -f "$PIDFILE"
        fi
        ;;
    --logs)
        if [ -f "$LOGFILE" ]; then
            tail -f "$LOGFILE"
        else
            echo "✗ No log file at $LOGFILE"
            exit 1
        fi
        ;;
    --restart)
        bash "$0" --stop 2>/dev/null || true
        sleep 1
        bash "$0" --bg
        ;;
    --help|-h)
        cat <<HELP
Àkànjí Oníṣòwò run script

Usage:
  bash run.sh                 # run in foreground (Ctrl+C to stop)
  bash run.sh --bg            # run in background, log to logs/akanji.log
  bash run.sh --status        # check if background bot is running
  bash run.sh --stop          # stop background bot
  bash run.sh --logs          # tail the log file
  bash run.sh --restart       # stop + start in background

Logs:        logs/akanji.log
PID file:    logs/akanji.pid
HELP
        ;;
    fg|"")
        # Foreground — Ctrl+C to stop
        echo "→ Starting bot in foreground (Ctrl+C to stop)…"
        exec .venv/bin/python main.py
        ;;
    *)
        echo "❌ Unknown option: $1"
        bash "$0" --help
        exit 1
        ;;
esac
