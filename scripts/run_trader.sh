#!/usr/bin/env bash
#
# scripts/run_trader.sh — Run the agent in the FOREGROUND.
#
# Use this for testing: you see all log output live, and Ctrl+C stops it
# cleanly. For a long-running background process, use start_bot.sh instead.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── Sanity checks ────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "ERROR: .env not found in $ROOT_DIR" >&2
    echo "Create .env with ALPACA_API_KEY / ALPACA_SECRET_KEY / etc. first." >&2
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "ERROR: venv/ not found. Run this first:" >&2
    echo "  python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' is not installed or not on PATH." >&2
    echo "The Alpaca MCP server is launched via 'uvx alpaca-mcp-server'," >&2
    echo "so uv is required. Install it: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate

mkdir -p logs data

echo "Starting agent in the foreground (Ctrl+C to stop)..."
echo "Mode: $(grep -E '^ALPACA_MODE=' .env | cut -d= -f2 || echo 'paper (default)')"
echo

exec python main.py