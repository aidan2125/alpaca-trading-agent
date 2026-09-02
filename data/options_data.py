# data/options_data.py
"""
Options market data fetcher for Alpaca — historical option contract bars.

Mirrors the pattern of the existing data/market_data.py::fetch_stock_ohlcv,
but targets OptionHistoricalDataClient instead of StockHistoricalDataClient
and returns a Polars DataFrame (this repo's backtest engine is Polars-based).

This was missing from the repo entirely before now — the options backtester
(backtest/options_backtest.py) needs it to pull real premium history for a
contract instead of guessing at prices.

Requires ALPACA_API_KEY / ALPACA_SECRET_KEY in the environment (same vars
fetch_stock_ohlcv already relies on).
"""

import os
import re
from datetime import datetime, timedelta
from typing import Optional

import polars as pl
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# Standard OCC option symbol: ROOT + YYMMDD + C/P + strike*1000 (8 digits)
# e.g. AAPL250117C00150000 -> AAPL, 2025-01-17, call, strike 150.00
_OCC_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})"
    r"(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<cp>[CP])"
    r"(?P<strike>\d{8})$"
)

_TIMEFRAME_MAP = {
    "1m": TimeFrame.Minute,
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "1h": TimeFrame.Hour,
    "1d": TimeFrame.Day,
}


def parse_occ_symbol(symbol: str) -> dict:
    """
    Decode a standard OCC option symbol into its underlying, expiration
    date, option type and strike.

    Returns {} if the symbol doesn't match the OCC format — callers should
    treat that as "metadata unavailable", not an error, since some feeds
    hand back contract fields already split out.
    """
    m = _OCC_RE.match(symbol.strip().upper())
    if not m:
        return {}
    yy, mm, dd = m.group("yy"), m.group("mm"), m.group("dd")
    expiration = datetime(2000 + int(yy), int(mm), int(dd)).date()
    return {
        "underlying": m.group("root"),
        "expiration": expiration,
        "option_type": "call" if m.group("cp") == "C" else "put",
        "strike": int(m.group("strike")) / 1000.0,
    }


def fetch_option_ohlcv(
    contract_symbol: str,
    timeframe: str = "1h",
    lookback_days: int = 30,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> pl.DataFrame:
    """
    Fetch historical bars for a single OCC option contract and return them
    as a Polars DataFrame with columns:

        timestamp, open, high, low, close, volume,
        expiration, dte, option_type, strike, underlying

    `close` is the contract's premium (not the underlying's price) — that's
    what OptionsBacktestPro expects. `dte` (days-to-expiration) is computed
    per-row from `timestamp` vs. the contract's expiration date, which is
    what the backtester's forced expiry-exit logic reads.

    Returns an empty pl.DataFrame() on any failure (no data, bad symbol,
    missing/invalid credentials) rather than raising — same contract as
    fetch_stock_ohlcv, so callers can handle both the same way.
    """
    api_key = api_key or os.getenv("ALPACA_API_KEY")
    secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ALPACA_API_KEY / ALPACA_SECRET_KEY not set — cannot fetch option data.")
        return pl.DataFrame()

    tf = _TIMEFRAME_MAP.get(timeframe)
    if tf is None:
        print(f"Unsupported timeframe '{timeframe}'. Supported: {list(_TIMEFRAME_MAP)}")
        return pl.DataFrame()

    meta = parse_occ_symbol(contract_symbol)
    if not meta:
        print(
            f"Warning: '{contract_symbol}' doesn't parse as a standard OCC symbol — "
            f"expiration/dte columns will be null. The backtester needs dte to force "
            f"the expiry exit, so fill it in yourself before running a backtest."
        )

    end = datetime.utcnow()
    start = end - timedelta(days=lookback_days)

    try:
        client = OptionHistoricalDataClient(api_key, secret_key)
        req = OptionBarsRequest(
            symbol_or_symbols=contract_symbol,
            timeframe=tf,
            start=start,
            end=end,
        )
        bar_set = client.get_option_bars(req)
    except Exception as e:
        print(f"Failed to fetch option bars for {contract_symbol}: {e}")
        return pl.DataFrame()

    bars = bar_set.data.get(contract_symbol, []) if hasattr(bar_set, "data") else []
    if not bars:
        print(f"No option bar data returned for {contract_symbol}.")
        return pl.DataFrame()

    expiration = meta.get("expiration")
    rows = []
    for b in bars:
        ts = b.timestamp
        dte = (expiration - ts.date()).days if expiration else None
        rows.append(
            {
                "timestamp": ts,
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
                "expiration": expiration,
                "dte": dte,
                "option_type": meta.get("option_type"),
                "strike": meta.get("strike"),
                "underlying": meta.get("underlying"),
            }
        )

    return pl.DataFrame(rows)