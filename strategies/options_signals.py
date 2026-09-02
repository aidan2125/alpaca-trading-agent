# strategies/options_signals.py
"""
Options signal generator — the piece backtest_agent.py was missing.

Pipeline:
  1. compute_underlying_signal()  — EMA(9)/EMA(21) crossover + ATR trend
     filter on the UNDERLYING's price (same shape as crypto-trader's
     ATR-based Enhanced Strategy, just self-contained here since the
     original enhanced_signals_refactored module isn't in this repo).
  2. select_option_contract()     — nearest-ATM contract at ~target_dte
     days out, using TradingClient.get_option_contracts. Direction from
     step 1 picks CALL vs PUT.
  3. build_signal_frame()         — as-of joins the underlying's signal
     timestamps onto the option contract's own premium timeline (they're
     different bar series), and maps directional signal -> the buy-only
     `signal` column OptionsBacktestPro expects (see options_backtest.py:
     it only acts on signal == 1, since selling to open isn't modeled).

ASSUMPTION FLAGGED: generate_options_backtest_frame() calls
`data.market_data.fetch_stock_ohlcv(symbol, timeframe, limit)` and expects
it back as a Polars DataFrame with timestamp/open/high/low/close columns.
I haven't seen that function's actual signature in this repo — if it takes
different args or returns pandas, adjust the one call site at the bottom
of this file; everything else here is independent of it.
"""

from datetime import date, timedelta
from typing import Optional

import polars as pl


# ────────────────────────────────────────────────
# 1. Direction from the underlying
# ────────────────────────────────────────────────
def compute_underlying_signal(
    df: pl.DataFrame,
    ema_fast: int = 9,
    ema_slow: int = 21,
    atr_period: int = 14,
) -> pl.DataFrame:
    """
    Adds ema_fast/ema_slow/atr/signal columns to an underlying OHLCV frame.

    signal = 1  (bullish -> call)  when fast EMA crosses above slow EMA
              AND close > slow EMA (trend filter, avoids chop signals)
    signal = -1 (bearish -> put)   on the mirrored crossunder
    signal = 0  otherwise

    Requires columns: timestamp, high, low, close.
    """
    required = {"timestamp", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_underlying_signal missing columns: {sorted(missing)}")

    df = df.sort("timestamp")

    # True range / ATR
    df = df.with_columns(
        [
            (pl.col("high") - pl.col("low")).alias("_tr1"),
            (pl.col("high") - pl.col("close").shift(1)).abs().alias("_tr2"),
            (pl.col("low") - pl.col("close").shift(1)).abs().alias("_tr3"),
        ]
    )
    df = df.with_columns(
        pl.max_horizontal(["_tr1", "_tr2", "_tr3"]).alias("_tr")
    )
    df = df.with_columns(
        pl.col("_tr").rolling_mean(window_size=atr_period).alias("atr")
    ).drop(["_tr1", "_tr2", "_tr3", "_tr"])

    df = df.with_columns(
        [
            pl.col("close").ewm_mean(span=ema_fast).alias("ema_fast"),
            pl.col("close").ewm_mean(span=ema_slow).alias("ema_slow"),
        ]
    )

    df = df.with_columns(
        [
            (pl.col("ema_fast") > pl.col("ema_slow")).alias("_bull_now"),
            (pl.col("ema_fast").shift(1) > pl.col("ema_slow").shift(1)).alias("_bull_prev"),
        ]
    )

    df = df.with_columns(
        pl.when((pl.col("_bull_now")) & (~pl.col("_bull_prev")) & (pl.col("close") > pl.col("ema_slow")))
        .then(1)
        .when((~pl.col("_bull_now")) & (pl.col("_bull_prev")) & (pl.col("close") < pl.col("ema_slow")))
        .then(-1)
        .otherwise(0)
        .alias("signal")
    ).drop(["_bull_now", "_bull_prev"])

    return df


# ────────────────────────────────────────────────
# 2. Contract selection
# ────────────────────────────────────────────────
def select_option_contract(
    underlying_symbol: str,
    direction: int,
    underlying_price: float,
    api_key: str,
    secret_key: str,
    target_dte: int = 30,
    dte_window: int = 7,
    strike_window_pct: float = 0.10,
    paper: bool = True,
) -> Optional[str]:
    """
    Returns the OCC symbol of the contract closest to ATM within
    [target_dte - dte_window, target_dte + dte_window] days out.
    direction: 1 -> call, -1 -> put. Returns None if nothing matches
    or direction == 0 (no trade signal to act on).
    """
    if direction == 0:
        return None

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetOptionContractsRequest
    from alpaca.trading.enums import ContractType, AssetStatus

    client = TradingClient(api_key, secret_key, paper=paper)

    today = date.today()
    req = GetOptionContractsRequest(
        underlying_symbols=[underlying_symbol],
        status=AssetStatus.ACTIVE,
        type=ContractType.CALL if direction == 1 else ContractType.PUT,
        expiration_date_gte=today + timedelta(days=max(target_dte - dte_window, 1)),
        expiration_date_lte=today + timedelta(days=target_dte + dte_window),
        strike_price_gte=str(round(underlying_price * (1 - strike_window_pct), 2)),
        strike_price_lte=str(round(underlying_price * (1 + strike_window_pct), 2)),
        limit=100,
    )

    try:
        contracts = client.get_option_contracts(req).option_contracts
    except Exception as e:
        print(f"Failed to fetch option contracts for {underlying_symbol}: {e}")
        return None

    if not contracts:
        print(
            f"No {'call' if direction == 1 else 'put'} contracts found for "
            f"{underlying_symbol} within {dte_window} days of {target_dte} DTE, "
            f"strikes near ${underlying_price:.2f}."
        )
        return None

    # Closest strike to spot; ties broken by closest to target_dte
    def _score(c):
        strike_dist = abs(float(c.strike_price) - underlying_price)
        dte_dist = abs((c.expiration_date - today).days - target_dte)
        return (strike_dist, dte_dist)

    best = min(contracts, key=_score)
    return best.symbol


# ────────────────────────────────────────────────
# 3. Merge underlying signal onto the option's own premium timeline
# ────────────────────────────────────────────────
def build_signal_frame(
    underlying_signal_df: pl.DataFrame,
    premium_df: pl.DataFrame,
    option_type: str,
) -> pl.DataFrame:
    """
    as-of joins the underlying's directional signal onto the option
    contract's premium bars (they're separate bar series with their own
    timestamps), then maps direction -> the buy-only `signal` column
    OptionsBacktestPro reads:

        call + bullish underlying signal (1)  -> signal = 1 (buy the call)
        put  + bearish underlying signal (-1) -> signal = 1 (buy the put)
        anything else                          -> signal = 0

    Both frames must be sorted by `timestamp` (compute_underlying_signal
    already sorts; premium_df from fetch_option_ohlcv comes pre-sorted
    from the API but is re-sorted here defensively).
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    underlying_sig = underlying_signal_df.select(["timestamp", "signal"]).rename(
        {"signal": "_underlying_signal"}
    ).sort("timestamp")
    premium_df = premium_df.sort("timestamp")

    merged = premium_df.join_asof(underlying_sig, on="timestamp", strategy="backward")

    target_direction = 1 if option_type == "call" else -1
    merged = merged.with_columns(
        pl.when(pl.col("_underlying_signal") == target_direction)
        .then(1)
        .otherwise(0)
        .alias("signal")
    ).drop("_underlying_signal")

    return merged


# ────────────────────────────────────────────────
# 4. One-call convenience wrapper
# ────────────────────────────────────────────────
def generate_options_backtest_frame(
    underlying_symbol: str,
    api_key: str,
    secret_key: str,
    timeframe: str = "1h",
    lookback_days: int = 60,
    target_dte: int = 30,
    dte_window: int = 7,
    paper: bool = True,
) -> tuple[Optional[pl.DataFrame], Optional[str]]:
    """
    Runs the full pipeline: fetch underlying -> direction -> pick contract
    -> fetch contract premium -> merge signal. Returns (df, contract_symbol)
    ready to hand straight to OptionsBacktestPro.run(), or (None, None) if
    no trade set up (no signal fired, or no matching contract).

    NOTE: the fetch_stock_ohlcv call below is written against the
    signature described in this repo's notes (symbol, timeframe, limit).
    Verify against the actual function in data/market_data.py — if it
    differs, this is the only place that needs to change.
    """
    from data.market_data import fetch_stock_ohlcv
    from data.options_data import fetch_option_ohlcv

    underlying_df = fetch_stock_ohlcv(underlying_symbol, timeframe, lookback_days * 24)
    if underlying_df is None or underlying_df.is_empty():
        print(f"No underlying data for {underlying_symbol}.")
        return None, None

    underlying_df = compute_underlying_signal(underlying_df)
    latest = underlying_df.tail(1)
    direction = int(latest["signal"][0])
    underlying_price = float(latest["close"][0])

    if direction == 0:
        print(f"No directional signal on {underlying_symbol} right now — nothing to trade.")
        return None, None

    contract_symbol = select_option_contract(
        underlying_symbol,
        direction,
        underlying_price,
        api_key,
        secret_key,
        target_dte=target_dte,
        dte_window=dte_window,
        paper=paper,
    )
    if contract_symbol is None:
        return None, None

    premium_df = fetch_option_ohlcv(contract_symbol, timeframe, lookback_days, api_key, secret_key)
    if premium_df.is_empty():
        print(f"No premium data for selected contract {contract_symbol}.")
        return None, None

    option_type = "call" if direction == 1 else "put"
    signal_df = build_signal_frame(underlying_df, premium_df, option_type)

    return signal_df, contract_symbol