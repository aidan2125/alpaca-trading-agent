"""
Market Data Fetching Module
Fetches OHLCV data from exchange using ccxt and returns Polars DataFrame.
"""

import logging
import os
import time
from typing import Optional

import ccxt
import polars as pl
from datetime import datetime, timezone

# Setup logging
logger = logging.getLogger(__name__)




# ─────────────────────────────────────────────────────────────────────────────
# Stock OHLCV via Alpaca
# ─────────────────────────────────────────────────────────────────────────────

def fetch_stock_ohlcv(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 500,
) -> Optional[pl.DataFrame]:
    """
    Fetch OHLCV data for a stock ticker via Alpaca Markets data API.
    Returns a Polars DataFrame with the same schema as fetch_ohlcv() so
    enhanced_strategy() can consume it without modification.

    Requires env vars:
        ALPACA_API_KEY
        ALPACA_SECRET_KEY
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.data.enums import DataFeed
    except ImportError:
        logger.error("alpaca-py not installed. Run: pip install alpaca-py")
        return None

    api_key    = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY / ALPACA_SECRET_KEY not set in environment")
        return None

    _TF_MAP = {
        "1m":  TimeFrame(1,  TimeFrameUnit.Minute),
        "5m":  TimeFrame(5,  TimeFrameUnit.Minute),
        "15m": TimeFrame(15, TimeFrameUnit.Minute),
        "30m": TimeFrame(30, TimeFrameUnit.Minute),
        "1h":  TimeFrame(1,  TimeFrameUnit.Hour),
        "2h":  TimeFrame(2,  TimeFrameUnit.Hour),
        "4h":  TimeFrame(4,  TimeFrameUnit.Hour),
        "1d":  TimeFrame(1,  TimeFrameUnit.Day),
    }
    alpaca_tf = _TF_MAP.get(timeframe.lower())
    if alpaca_tf is None:
        logger.error(f"Unsupported timeframe for stocks: '{timeframe}'. Use one of {list(_TF_MAP)}")
        return None

    try:
        client = StockHistoricalDataClient(api_key, secret_key)

        timeframe_ms = _timeframe_to_ms(timeframe)
        now_ms       = int(time.time() * 1000)
        start_ms     = now_ms - (limit * timeframe_ms)
        start_dt     = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=alpaca_tf,
            start=start_dt,
            limit=limit,
            feed=DataFeed.IEX,
        )

        bars     = client.get_stock_bars(request)
        try:
            bar_list = bars[symbol]
        except KeyError:
            logger.warning(f"[stocks] No data returned for {symbol}")
            return None

        if not bar_list:
            logger.warning(f"[stocks] No data returned for {symbol}")
            return None

        rows = [
            [
                int(bar.timestamp.timestamp() * 1000),
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                float(bar.volume),
            ]
            for bar in bar_list
        ]

        unique: dict[int, list] = {}
        for row in rows:
            unique[row[0]] = row
        rows = sorted(unique.values(), key=lambda r: r[0])
        rows = rows[-limit:]

        df = pl.DataFrame(
            rows,
            schema=[
                ("timestamp", pl.Int64),
                ("open",      pl.Float64),
                ("high",      pl.Float64),
                ("low",       pl.Float64),
                ("close",     pl.Float64),
                ("volume",    pl.Float64),
            ],
            orient="row",
        )

        df = df.with_columns(
            pl.col("timestamp")
            .cast(pl.Datetime(time_unit="ms", time_zone="UTC"))
            .alias("timestamp")
        )

        df = df.sort("timestamp")

        logger.info(f"[stocks] Fetched {df.height} bars for {symbol} ({timeframe})")
        print(f"[stocks] {symbol}: {df.height} bars fetched")
        return df

    except Exception as e:
        logger.exception(f"[stocks] Error fetching {symbol}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Quick standalone test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    df = fetch_ohlcv("BTC/USDT", "1h", 3000)

    if df is not None:
        print(df.head(5))
        print(f"\nShape:   {df.shape}")
        print(f"Columns: {df.columns}")
    else:
        print("Failed to fetch data.")