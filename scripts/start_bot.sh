#!/usr/bin/env bash
#
# scripts/start_bot.sh — Start the agent as a BACKGROUND process.
#
# Logs go to logs/bot.out. PID is tracked in run/bot.pid so stop_bot.sh
# knows what to kill. Safe to re-run: refuses to start a second copy if
# one is already running.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="run/bot.pid"
LOG_FILE="logs/bot.out"

mkdir -p run logs data

# ── Refuse to double-start ───────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE")"
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Agent already running (PID $OLD_PID). Use stop_bot.sh first." >&2
        exit 1
    else
        echo "Stale PID file found (process $OLD_PID not running) — cleaning up."
        rm -f "$PID_FILE"
    fi
fi

# ── Sanity checks ────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "ERROR: .env not found in $ROOT_DIR" >&2
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "ERROR: venv/ not found. Run:" >&2
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' is not installed or not on PATH (needed for 'uvx alpaca-mcp-server')." >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

# ── Launch ───────────────────────────────────────────────────────────────
nohup python main.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

# Give it a moment, then confirm it didn't die immediately (e.g. bad env,
# import error, MCP server failed to spawn).
sleep 2
if ! kill -0 "$NEW_PID" 2>/dev/null; then
    echo "ERROR: agent exited immediately. Check $LOG_FILE:" >&2
    tail -n 30 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
fi

echo "Agent started (PID $NEW_PID). Logs: $LOG_FILE"
echo "Stop it with: scripts/stop_bot.sh"