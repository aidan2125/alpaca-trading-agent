"""
config/settings.py — Central settings loader.
Loads environment variables from .env while providing safe defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


# Load .env from project root
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


# ── Trading mode ─────────────────────────────────────────────────────────────

TRADING_MODE = os.getenv("TRADING_MODE", "paper").lower()


# ── Alpaca ──────────────────────────────────────────────────────────────────

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

ALPACA_MODE = os.getenv("ALPACA_MODE", "paper")
ALPACA_PAPER = ALPACA_MODE.lower() != "live"

ALPACA_BASE_URL = os.getenv(
    "ALPACA_BASE_URL",
    "https://paper-api.alpaca.markets"
    if ALPACA_PAPER
    else "https://api.alpaca.markets",
)


# ── Options trading defaults ────────────────────────────────────────────────

OPTIONS_UNIVERSE = [
    s.strip().upper()
    for s in os.getenv("OPTIONS_UNIVERSE", "SPY,QQQ,AAPL").split(",")
    if s.strip()
]

OPTIONS_MIN_DTE = int(os.getenv("OPTIONS_MIN_DTE", "7"))
OPTIONS_MAX_DTE = int(os.getenv("OPTIONS_MAX_DTE", "45"))
OPTIONS_TRADING_LEVEL = int(os.getenv("OPTIONS_TRADING_LEVEL", "3"))
OPTIONS_CONTRACT_MULTIPLIER = int(
    os.getenv("OPTIONS_CONTRACT_MULTIPLIER", "100")
)


# ── LLM ─────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")

FEATHERLESS_BASE_URL = os.getenv(
    "FEATHERLESS_BASE_URL",
    "https://api.featherless.ai/v1",
)

FEATHERLESS_MODEL = os.getenv(
    "FEATHERLESS_MODEL",
    "zai-org/GLM-5.2",
)


# ── Alerts ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")


# ── Database ────────────────────────────────────────────────────────────────

TRADE_DB_PATH = os.getenv(
    "TRADE_DB_PATH",
    "data/trades.db",
)


# ── Risk defaults ───────────────────────────────────────────────────────────

RISK_PRESET = os.getenv(
    "RISK_PRESET",
    "conservative",
)

MAX_RISK_PER_TRADE = float(
    os.getenv("MAX_RISK_PER_TRADE", "0.01")
)

MAX_POSITIONS = int(
    os.getenv("MAX_POSITIONS", "3")
)

DAILY_LOSS_LIMIT = float(
    os.getenv("DAILY_LOSS_LIMIT", "0.05")
)

MAX_POSITION_PCT = float(
    os.getenv("MAX_POSITION_PCT", "0.10")
)

TRADE_COOLDOWN_HOURS = float(
    os.getenv("TRADE_COOLDOWN_HOURS", "4")
)

MAX_TRADES_PER_DAY = int(
    os.getenv("MAX_TRADES_PER_DAY", "6")
)

MIN_SIGNAL_QUALITY = float(
    os.getenv("MIN_SIGNAL_QUALITY", "60")
)


# ── Bot loop ────────────────────────────────────────────────────────────────

BOT_INTERVAL_SECONDS = int(
    os.getenv("BOT_INTERVAL_SECONDS", "300")
)


# ── Logging ─────────────────────────────────────────────────────────────────

LOG_DIR = os.getenv(
    "LOG_DIR",
    "logs",
)

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
)