"""
config/settings.py  —  Central settings loader.
Priority: CLI args > .env overrides > defaults.yaml > hardcoded defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_root = Path(__file__).parent.parent
load_dotenv(_root / '.env')

# ── Trading mode ──────────────────────────────────────────────────────────────
TRADING_MODE = os.getenv('TRADING_MODE', 'paper').lower()  # 'paper' | 'live'

# ── Alpaca ────────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv('ALPACA_API_KEY', '')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY', '')
ALPACA_PAPER      = os.getenv('ALPACA_MODE', 'paper').lower() != 'live'
# Base URL is derived from ALPACA_PAPER by the SDK/MCP server; kept here only
# for any code path that needs it directly.
ALPACA_BASE_URL   = os.getenv(
    'ALPACA_BASE_URL',
    'https://paper-api.alpaca.markets' if ALPACA_PAPER else 'https://api.alpaca.markets',
)

# ── Options trading defaults ──────────────────────────────────────────────────
# Underlyings the agent is allowed to consider for options strategies.
OPTIONS_UNIVERSE       = [s.strip().upper() for s in os.getenv('OPTIONS_UNIVERSE', 'SPY,QQQ,AAPL').split(',') if s.strip()]
# Days-to-expiration window the agent should search within when pulling contracts.
OPTIONS_MIN_DTE        = int(os.getenv('OPTIONS_MIN_DTE', '7'))
OPTIONS_MAX_DTE        = int(os.getenv('OPTIONS_MAX_DTE', '45'))
# Max options trading level to request/assume (Alpaca levels 1-3; 3 = multi-leg spreads).
OPTIONS_TRADING_LEVEL  = int(os.getenv('OPTIONS_TRADING_LEVEL', '3'))
# Standard contract multiplier (shares per contract) used by risk/position sizing.
OPTIONS_CONTRACT_MULTIPLIER = int(os.getenv('OPTIONS_CONTRACT_MULTIPLIER', '100'))

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '')

# ── Discord (optional) ────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')

# ── Database ──────────────────────────────────────────────────────────────────
TRADE_DB_PATH = os.getenv('TRADE_DB_PATH', 'data/trades.db')

# ── Risk defaults (beginner-safe) ─────────────────────────────────────────────
RISK_PRESET          = os.getenv('RISK_PRESET', 'conservative')
MAX_RISK_PER_TRADE   = float(os.getenv('MAX_RISK_PER_TRADE', '0.01'))   # 1% of equity per trade (premium at risk)
MAX_POSITIONS        = int(os.getenv('MAX_POSITIONS', '3'))
DAILY_LOSS_LIMIT     = float(os.getenv('DAILY_LOSS_LIMIT', '0.05'))     # 5%
MAX_POSITION_PCT     = float(os.getenv('MAX_POSITION_PCT', '0.10'))     # 10%
TRADE_COOLDOWN_HOURS = float(os.getenv('TRADE_COOLDOWN_HOURS', '4'))
MAX_TRADES_PER_DAY   = int(os.getenv('MAX_TRADES_PER_DAY', '6'))
MIN_SIGNAL_QUALITY   = float(os.getenv('MIN_SIGNAL_QUALITY', '60'))

# ── Bot loop ──────────────────────────────────────────────────────────────────
BOT_INTERVAL_SECONDS = int(os.getenv('BOT_INTERVAL', '300'))   # 5 minutes

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR   = os.getenv('LOG_DIR', 'logs')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')