#!/usr/bin/env python3
# backtest/options_backtest.py
"""
Options Backtester — repurposed from backtest/backtest.py (the crypto/stock
spot engine) for options.

What's different from the spot BacktestPro, and why:

  - P&L is computed on OPTION PREMIUM, not the underlying's price. `close`
    in the input dataframe must be the contract's premium (mark/last), not
    the underlying's price.
  - Sizing is in CONTRACTS, not raw USD. Each contract represents
    `contract_multiplier` (100 by default) units of the underlying, so
    every premium delta is multiplied by that when computing dollar P&L —
    a $0.50 move on a 100x contract is $50, not $0.50.
  - Adds a forced EXPIRY exit: any open position is closed once `dte`
    (days-to-expiration) drops to or below `expiry_exit_dte`, independent
    of SL/TP, because options carry a hard time boundary a spot position
    never has. New entries are also blocked once there isn't enough
    runway left to expiry.
  - No Supabase. Results print to stdout and are returned as a dict from
    `run()` / `print_summary()`. Wire your own persistence (SQLite
    TradeLogger, a CSV dump, whatever the live side already uses) around
    that return value or around `.trades` directly.

Scope: this models buying long calls/puts (debit positions) — max loss is
capped at the premium paid, which is what makes fixed-fractional contract
sizing well-defined from `risk_per_trade` alone. Selling options (credit
spreads, covered calls, cash-secured puts, etc.) needs margin-based sizing
instead of premium-based sizing and isn't modeled here — extend the sizing
block in `_open_position` if you need that.

Note on `signal`: unlike the spot engine, a bare "short" signal on a single
premium series isn't a well-defined trade — selling to open needs different
sizing/margin (see above). This engine only acts on signal == 1 (buy calls
or puts, whichever contract the dataframe represents) and skips -1 signals
rather than silently mis-modeling a short. To backtest a strategy that
picks between calls and puts, run separate call/put premium series through
separate `run()` calls and compare.

Expected input dataframe (Polars) columns:
  timestamp       - bar time
  close           - option premium                                [required]
  signal          - 1 (buy to open) / -1 (ignored) / 0 (no action) [required]
  dte             - days-to-expiration as of this bar              [required]
  stop_loss       - premium level that exits at a loss             [optional]
  take_profit     - premium level that exits at a profit           [optional]
  signal_quality  - 0-100 confidence score used to scale size      [optional]

If stop_loss/take_profit are missing, they fall back to a percentage of the
entry premium (default: exit at -50% / +100%) — the standard rule-of-thumb
risk bounds for long premium, since there's no ATR-on-the-underlying
equivalent that translates cleanly onto premium.
"""

import argparse
import json
from pathlib import Path

import polars as pl


# ────────────────────────────────────────────────
# Risk config (unchanged from the spot backtester — still just risk_per_trade)
# ────────────────────────────────────────────────
RISK_CONFIG_PATH = Path("data") / "risk_config.json"


def load_risk_config() -> dict:
    default_config = {
        "risk_per_trade": 0.01,
        "min_signal_quality": 60,
    }
    if RISK_CONFIG_PATH.exists():
        try:
            with open(RISK_CONFIG_PATH, "r") as f:
                default_config.update(json.load(f))
                print(f"Loaded risk config: {RISK_CONFIG_PATH}")
                print(f"   Risk per trade: {default_config['risk_per_trade']:.1%}\n")
        except Exception as e:
            print(f"Could not load risk config: {e}. Using defaults.")
    else:
        print("No risk_config.json found — using default settings.\n")
    return default_config


# ────────────────────────────────────────────────
# Backtest engine
# ────────────────────────────────────────────────
class OptionsBacktestPro:
    def __init__(
        self,
        initial_balance: float = 10000,
        risk_config: dict | None = None,
        contract_multiplier: int = 100,
        expiry_exit_dte: int = 1,
        default_stop_loss_pct: float = 0.50,
        default_take_profit_pct: float = 1.00,
    ):
        self.initial_balance = float(initial_balance)
        self.balance = float(initial_balance)
        self.equity = []
        self.trades = []
        self.position = None
        self.risk_config = risk_config or {}
        self.contract_multiplier = contract_multiplier
        self.expiry_exit_dte = expiry_exit_dte
        self.default_stop_loss_pct = default_stop_loss_pct
        self.default_take_profit_pct = default_take_profit_pct

    def run(
        self,
        df: pl.DataFrame,
        symbol: str = "OPTION",
        strategy_name: str = "Options Strategy",
    ) -> dict:
        if df.is_empty():
            print("No data to backtest.")
            return {"trades": 0}

        required = {"close", "signal", "dte"}
        missing = required - set(df.columns)
        if missing:
            print(f"Missing required columns: {sorted(missing)}")
            return {"trades": 0}

        print("=== OPTIONS BACKTEST START ===")
        print(f"Contract         : {symbol}")
        print(f"Valid candles    : {df.height}")
        print(f"Strategy         : {strategy_name}")
        print(f"Initial Balance  : ${self.initial_balance:,.0f}")
        print(f"Contract mult.   : {self.contract_multiplier}")
        print(f"Expiry exit DTE  : <= {self.expiry_exit_dte}\n")

        columns = df.columns
        close_idx = columns.index("close")
        signal_idx = columns.index("signal")
        dte_idx = columns.index("dte")
        sl_idx = columns.index("stop_loss") if "stop_loss" in columns else -1
        tp_idx = columns.index("take_profit") if "take_profit" in columns else -1
        quality_idx = columns.index("signal_quality") if "signal_quality" in columns else -1

        risk_pct = self.risk_config.get("risk_per_trade", 0.01)

        for i in range(1, df.height):
            row = df.row(i)
            premium = float(row[close_idx])
            dte_raw = row[dte_idx]
            dte = float(dte_raw) if dte_raw is not None else None

            # Mark-to-market the open position before any exit/entry logic runs
            if self.position:
                pos = self.position
                pnl = (premium - pos["entry"]) * pos["contracts"] * self.contract_multiplier
                pos["value"] = pos["cost"] + pnl

            # Expiry is a hard constraint — check it before SL/TP, which are
            # strategy choices, not physical limits.
            if self.position and dte is not None and dte <= self.expiry_exit_dte:
                self._close_position(premium, "EXPIRY")

            if self.position:
                self._check_exit(premium, sl_idx, tp_idx)

            signal = int(row[signal_idx]) if row[signal_idx] is not None else 0
            enough_runway = dte is None or dte > self.expiry_exit_dte
            if signal == 1 and self.position is None and self.balance > 50 and enough_runway:
                self._open_position(row, premium, risk_pct, sl_idx, tp_idx, quality_idx)

            current_value = self.balance + (self.position["value"] if self.position else 0)
            self.equity.append(current_value)

        if self.position:
            final_premium = float(df["close"].tail(1)[0])
            self._close_position(final_premium, "END_OF_DATA")

        return self.print_summary(symbol, strategy_name)

    def _open_position(self, row, premium, risk_pct, sl_idx, tp_idx, quality_idx):
        entry_premium = premium * 1.0005  # slippage on the ask

        sl_price = self._read_optional_float(row, sl_idx)
        tp_price = self._read_optional_float(row, tp_idx)

        if sl_price is None:
            sl_price = entry_premium * (1 - self.default_stop_loss_pct)
        if tp_price is None:
            tp_price = entry_premium * (1 + self.default_take_profit_pct)

        risk_per_contract = (entry_premium - sl_price) * self.contract_multiplier
        if risk_per_contract <= 0:
            return  # SL at or above entry — refuse to size an undefined-risk trade

        signal_quality = 60.0
        if quality_idx >= 0 and row[quality_idx] is not None:
            try:
                signal_quality = float(row[quality_idx])
            except (ValueError, TypeError):
                pass

        if signal_quality >= 80:
            risk_pct_adj = risk_pct * 1.5
        elif signal_quality >= 60:
            risk_pct_adj = risk_pct
        else:
            risk_pct_adj = risk_pct * 0.5

        risk_amount = self.balance * risk_pct_adj
        contracts = int(risk_amount // risk_per_contract)

        cost = contracts * entry_premium * self.contract_multiplier
        max_affordable = self.balance * 0.50  # cap: no single trade over 50% of balance
        while contracts > 1 and cost > max_affordable:
            contracts -= 1
            cost = contracts * entry_premium * self.contract_multiplier

        if contracts < 1 or cost > self.balance:
            return

        entry_fee = cost * 0.001
        cost += entry_fee

        self.position = {
            "entry": entry_premium,
            "contracts": contracts,
            "cost": cost,
            "value": cost,
            "sl": sl_price,
            "tp": tp_price,
        }
        self.balance -= cost
        print(
            f"BUY {contracts} contract(s) @ ${entry_premium:.2f} premium | "
            f"Cost: ${cost:,.0f} | SL: ${sl_price:.2f} | TP: ${tp_price:.2f}"
        )

    @staticmethod
    def _read_optional_float(row, idx):
        if idx < 0 or row[idx] is None:
            return None
        try:
            return float(row[idx])
        except (ValueError, TypeError):
            return None

    def _check_exit(self, premium, sl_idx, tp_idx):
        pos = self.position
        hit_sl = premium <= pos["sl"]
        hit_tp = premium >= pos["tp"]
        if hit_sl or hit_tp:
            exit_premium = premium * 0.9995  # slippage on the bid
            self._close_position(exit_premium, "SL" if hit_sl else "TP")

    def _close_position(self, exit_premium, reason):
        pos = self.position
        gross_pnl = (exit_premium - pos["entry"]) * pos["contracts"] * self.contract_multiplier
        proceeds_before_fee = pos["contracts"] * exit_premium * self.contract_multiplier
        exit_fee = proceeds_before_fee * 0.001
        pnl = gross_pnl - exit_fee

        self.balance += proceeds_before_fee - exit_fee
        pnl_pct = (pnl / pos["cost"] * 100) if pos["cost"] > 0 else 0

        self.trades.append(
            {
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "contracts": pos["contracts"],
                "entry": pos["entry"],
                "exit": exit_premium,
            }
        )
        print(
            f"SELL {pos['contracts']} contract(s) @ ${exit_premium:.2f} | "
            f"PnL: ${pnl:+,.0f} ({pnl_pct:+.1f}%) | {reason}"
        )
        self.position = None

    def print_summary(self, symbol: str, strategy_name: str) -> dict:
        if not self.trades:
            print("\nNo trades executed — check signal generation or filters.")
            return {"trades": 0}

        df_trades = pl.DataFrame(self.trades)
        total_pnl = df_trades["pnl"].sum()
        roi = (self.balance / self.initial_balance - 1) * 100
        win_rate = df_trades.filter(pl.col("pnl") > 0).height / df_trades.height
        gross_profit = df_trades.filter(pl.col("pnl") > 0)["pnl"].sum()
        gross_loss = abs(df_trades.filter(pl.col("pnl") <= 0)["pnl"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        equity_curve = pl.Series([self.initial_balance] + self.equity)
        max_dd = (
            (equity_curve / equity_curve.cum_max() - 1).min()
            if equity_curve.len() > 1
            else 0.0
        )

        sl_count = df_trades.filter(pl.col("reason") == "SL").height
        tp_count = df_trades.filter(pl.col("reason") == "TP").height
        expiry_count = df_trades.filter(pl.col("reason") == "EXPIRY").height

        print("\n" + "═" * 70)
        print(f" OPTIONS BACKTEST SUMMARY - {symbol.upper()}")
        print("═" * 70)
        print(f"Strategy         : {strategy_name}")
        print(f"Total Trades     : {df_trades.height}")
        print(f"Win Rate         : {win_rate:.1%}")
        print(f"Profit Factor    : {profit_factor:.2f}")
        print(f"SL / TP / EXPIRY : {sl_count} / {tp_count} / {expiry_count}")
        print(f"Total PnL        : ${total_pnl:+,.0f}")
        print(f"Final Balance    : ${self.balance:,.0f}")
        print(f"ROI              : {roi:+.1f}%")
        print(f"Max Drawdown     : {max_dd:.1%}")
        print(f"Avg PnL/Trade    : ${df_trades['pnl'].mean():+.0f}")
        print("═" * 70)

        return {
            "trades": df_trades.height,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": float(total_pnl),
            "final_balance": self.balance,
            "roi": roi,
            "max_drawdown": float(max_dd),
        }


# ────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest an options strategy against historical premium data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--underlying", type=str, default=None,
        help="Underlying stock symbol, e.g. AAPL. When set, runs the full pipeline: "
             "underlying EMA/ATR direction -> nearest-ATM contract selection -> "
             "backtest. Takes priority over --contract.",
    )
    parser.add_argument(
        "--contract", type=str, default=None,
        help="OCC option symbol, e.g. AAPL250117C00150000. Used only when "
             "--underlying isn't given — fetches premium data with no signal "
             "generator attached (needs --csv, or will produce zero trades).",
    )
    parser.add_argument("--timeframe", type=str, default="1h")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--target-dte", type=int, default=30,
                        help="Target days-to-expiration for contract auto-selection.")
    parser.add_argument("--dte-window", type=int, default=7,
                        help="+/- days around --target-dte to search for a contract.")
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Load pre-built premium+signal data from a CSV instead of fetching "
             "from Alpaca. Must contain close, signal, dte (and optionally "
             "stop_loss/take_profit/signal_quality). Takes priority over both "
             "--underlying and --contract.",
    )
    parser.add_argument("--expiry-exit-dte", type=int, default=1)
    return parser.parse_args()


if __name__ == "__main__":
    import os

    args = parse_args()
    risk_config = load_risk_config()
    symbol_label = args.contract or args.underlying or "OPTION"

    if args.csv:
        df = pl.read_csv(args.csv, try_parse_dates=True)

    elif args.underlying:
        from strategies.options_signals import generate_options_backtest_frame

        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        print(f"Running underlying -> contract -> signal pipeline for {args.underlying}...")
        df, picked_contract = generate_options_backtest_frame(
            args.underlying,
            api_key,
            secret_key,
            timeframe=args.timeframe,
            lookback_days=args.lookback_days,
            target_dte=args.target_dte,
            dte_window=args.dte_window,
        )
        if df is None:
            df = pl.DataFrame()
        else:
            symbol_label = picked_contract

    elif args.contract:
        from data.options_data import fetch_option_ohlcv

        print(f"Fetching option bars for {args.contract}...")
        df = fetch_option_ohlcv(args.contract, args.timeframe, args.lookback_days)

        if not df.is_empty() and "signal" not in df.columns:
            print(
                "\nFetched premium data has no 'signal' column — pass --underlying "
                "to run the full signal pipeline, or --csv with signal/stop_loss/"
                "take_profit already computed."
            )
            df = pl.DataFrame()

    else:
        print("Need one of --csv, --underlying, or --contract.")
        df = pl.DataFrame()

    if df.is_empty():
        print("No data to backtest.")
    else:
        backtester = OptionsBacktestPro(
            initial_balance=10000,
            risk_config=risk_config,
            expiry_exit_dte=args.expiry_exit_dte,
        )
        backtester.run(df, symbol=symbol_label, strategy_name="Options Strategy")