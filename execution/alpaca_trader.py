"""
execution/alpaca_trader.py

Alpaca broker executor for stocks.
Mirrors the interface of enhanced_paper_trader.py so main_enhanced.py
can call execute_stock_trade() the same way it calls execute_paper_trade().

Starts in PAPER mode by default (safe).
Switch to live by changing ALPACA_MODE in your .env to "live"
and updating the base URL — your bot code doesn't change at all.

─────────────────────────────────────────────────────────────────────────────
.env keys to add (same pattern as your existing exchange keys):

    ALPACA_API_KEY=your_api_key_here        # ← paste from alpaca.markets dashboard
    ALPACA_SECRET_KEY=your_secret_key_here  # ← paste from alpaca.markets dashboard
    ALPACA_MODE=paper                       # ← keep as "paper" until you're confident

─────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Alpaca SDK ────────────────────────────────────────────────────────────────
# Install: pip install alpaca-py
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
    from alpaca.data.historical import StockHistoricalDataClient
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    logging.warning("[alpaca_trader] alpaca-py not installed. Run: pip install alpaca-py")

# ── Config from .env ──────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# PUT YOUR ALPACA KEYS IN .env — see header above
# These lines read them automatically; you do NOT paste keys here.
# ─────────────────────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")      # ← key goes in .env
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")   # ← secret goes in .env
ALPACA_MODE       = os.getenv("ALPACA_MODE", "paper")     # "paper" or "live"

IS_PAPER = ALPACA_MODE.lower() != "live"

# ── Local trade log (mirrors your existing JSON pattern) ──────────────────────
STOCK_TRADES_FILE    = "data/stock_trades.json"
STOCK_POSITIONS_FILE = "data/stock_positions.json"
STOCK_BALANCE_FILE   = "data/stock_balance.json"

INITIAL_PAPER_BALANCE = 100_000.0   # Alpaca paper account default


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — same load_json / save_json pattern as enhanced_paper_trader.py
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path: str, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Alpaca client — initialised once, reused
# ─────────────────────────────────────────────────────────────────────────────

def _get_client() -> "TradingClient | None":
    if not ALPACA_AVAILABLE:
        return None
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        logging.error(
            "[alpaca_trader] ALPACA_API_KEY / ALPACA_SECRET_KEY not set in .env"
        )
        return None
    try:
        return TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=IS_PAPER,           # True → paper endpoint, False → live endpoint
        )
    except Exception as e:
        logging.error(f"[alpaca_trader] Failed to create TradingClient: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Core executor — called from main_enhanced.py run_for_stock()
# Mirrors execute_paper_trade() signature as closely as possible
# ─────────────────────────────────────────────────────────────────────────────

def execute_stock_trade(
    ticker: str,
    signal: int,
    price: float,
    currency: str = "USD",
    atr: float | None = None,
    risk_config: dict | None = None,
    override_risk: bool = False,
) -> str | None:
    """
    Execute a stock trade via Alpaca (paper or live).

    Args:
        ticker:      Stock ticker e.g. "TQQQ"
        signal:      1 = BUY, -1 = SELL/close, 0 = HOLD (no action)
        price:       Current price (used for position sizing)
        currency:    Always "USD" for stocks
        atr:         ATR value for stop-loss/take-profit calculation
        risk_config: Dict from load_config() — uses max_loss_per_trade_pct etc.
        override_risk: If True, force-close regardless of signal (used by SL/TP checker)

    Returns:
        Human-readable result string, or None if no action taken.
    """
    if signal == 0 and not override_risk:
        return None

    client = _get_client()
    positions = _load_json(STOCK_POSITIONS_FILE, {})
    balance_data = _load_json(STOCK_BALANCE_FILE, {"balance": INITIAL_PAPER_BALANCE})
    balance = balance_data.get("balance", INITIAL_PAPER_BALANCE)

    rc = risk_config or {}
    risk_pct       = rc.get("risk_per_trade", rc.get("max_loss_per_trade_pct", 0.02))
    atr_sl_mult    = rc.get("atr_multiplier_sl", 2.0)
    atr_tp_mult    = rc.get("atr_multiplier_tp", 3.0)
    max_positions  = rc.get("max_positions", 5)

    # ── BUY ──────────────────────────────────────────────────────────────────
    if signal == 1:
        if ticker in positions:
            msg = f"[SKIP] Already holding {ticker}"
            return {"success": False, "action": "BUY", "message": msg, "quantity": 0,
                    "size_usd": None, "stop_loss": None, "take_profit": None,
                    "pnl": None, "exit_reason": None}

        if len(positions) >= max_positions:
            msg = f"[SKIP] Max positions ({max_positions}) reached"
            return {"success": False, "action": "BUY", "message": msg, "quantity": 0,
                    "size_usd": None, "stop_loss": None, "take_profit": None,
                    "pnl": None, "exit_reason": None}

        # Position sizing — same ATR-based logic as your paper trader
        if atr and atr > 0:
            risk_dollars  = balance * risk_pct
            stop_distance = atr * atr_sl_mult
            shares        = max(1, int(risk_dollars / stop_distance))
        else:
            # Fallback: flat 2% of balance
            shares = max(1, int((balance * risk_pct) / price))

        cost = shares * price
        if cost > balance:
            shares = max(1, int(balance * 0.95 / price))   # Use 95% of balance max
            cost   = shares * price

        # Calculate SL/TP levels for local tracking
        stop_loss   = (price - atr * atr_sl_mult) if atr else (price * 0.97)
        take_profit = (price + atr * atr_tp_mult) if atr else (price * 1.06)

        # Place order via Alpaca
        order_result = _place_order(
            client=client,
            ticker=ticker,
            side=OrderSide.BUY if ALPACA_AVAILABLE else "buy",
            qty=shares,
            ticker_label=ticker,
        )

        if order_result:
            # Record position locally (same pattern as your POSITIONS_FILE)
            positions[ticker] = {
                "entry_price":  price,
                "shares":       shares,
                "cost":         cost,
                "stop_loss":    round(stop_loss, 4),
                "take_profit":  round(take_profit, 4),
                "currency":     currency,
                "entry_time":   datetime.now(timezone.utc).isoformat(),
                "atr_at_entry": atr,
                "order_id":     order_result,
            }
            balance -= cost
            balance_data["balance"] = balance
            _save_json(STOCK_POSITIONS_FILE, positions)
            _save_json(STOCK_BALANCE_FILE, balance_data)

            result = (
                f"BUY {shares} × {ticker} @ ${price:.2f} | "
                f"SL: ${stop_loss:.2f} | TP: ${take_profit:.2f} | "
                f"Cost: ${cost:.2f} | Mode: {'PAPER' if IS_PAPER else 'LIVE'}"
            )
            logging.info(f"[alpaca_trader] {result}")
            _log_trade(ticker, "BUY", shares, price, stop_loss, take_profit, cost)
            return {"success": True, "action": "BUY", "message": result, "quantity": shares,
                    "size_usd": cost, "stop_loss": stop_loss, "take_profit": take_profit,
                    "pnl": None, "exit_reason": None}
        else:
            msg = f"[ERROR] Order failed for {ticker} BUY"
            return {"success": False, "action": "BUY", "message": msg, "quantity": 0,
                    "size_usd": None, "stop_loss": stop_loss, "take_profit": take_profit,
                    "pnl": None, "exit_reason": None}

    # ── SELL / Close ──────────────────────────────────────────────────────────
    elif signal == -1 or override_risk:
        if ticker not in positions:
            msg = f"[SKIP] No position in {ticker} to close"
            return {"success": False, "action": "SELL", "message": msg, "quantity": 0,
                    "size_usd": None, "stop_loss": None, "take_profit": None,
                    "pnl": None, "exit_reason": None}

        position = positions[ticker]
        shares       = position.get("shares", 1)
        entry_price  = position.get("entry_price", price)
        cost         = position.get("cost", 0)
        proceeds     = shares * price
        pnl          = proceeds - cost
        pnl_pct      = (pnl / cost * 100) if cost > 0 else 0

        order_result = _place_order(
            client=client,
            ticker=ticker,
            side=OrderSide.SELL if ALPACA_AVAILABLE else "sell",
            qty=shares,
            ticker_label=ticker,
        )

        if order_result:
            del positions[ticker]
            balance += proceeds
            balance_data["balance"] = balance
            _save_json(STOCK_POSITIONS_FILE, positions)
            _save_json(STOCK_BALANCE_FILE, balance_data)

            result = (
                f"SELL {shares} × {ticker} @ ${price:.2f} | "
                f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%) | "
                f"Balance: ${balance:.2f} | Mode: {'PAPER' if IS_PAPER else 'LIVE'}"
            )
            logging.info(f"[alpaca_trader] {result}")
            _log_trade(ticker, "SELL", shares, price, pnl=pnl)
            return {"success": True, "action": "SELL", "message": result, "quantity": shares,
                    "size_usd": cost, "stop_loss": None, "take_profit": None, "pnl": pnl,
                    "exit_reason": "MANUAL"}
        else:
            msg = f"[ERROR] Order failed for {ticker} SELL"
            return {"success": False, "action": "SELL", "message": msg, "quantity": 0,
                    "size_usd": None, "stop_loss": None, "take_profit": None,
                    "pnl": None, "exit_reason": None}

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Alpaca order placement
# ─────────────────────────────────────────────────────────────────────────────

def _place_order(client, ticker: str, side, qty: int, ticker_label: str) -> str | None:
    """
    Submit a market order to Alpaca. Returns order ID string on success, None on failure.
    Falls back to a simulated order ID when alpaca-py is not installed
    (so you can test the flow without the SDK).
    """
    if not ALPACA_AVAILABLE or client is None:
        # Simulate — useful for dry-run testing before SDK is installed
        fake_id = f"SIM-{ticker_label}-{int(time.time())}"
        logging.info(f"[alpaca_trader] SIMULATED order {fake_id} ({side} {qty} {ticker})")
        return fake_id

    try:
        order_data = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,   # DAY order — expires at market close
        )
        order = client.submit_order(order_data=order_data)
        logging.info(f"[alpaca_trader] Order submitted: {order.id} | {side} {qty} {ticker}")
        return str(order.id)
    except Exception as e:
        logging.error(f"[alpaca_trader] Order failed ({ticker} {side}): {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Trade log — mirrors summarize_paper_trades() pattern
# ─────────────────────────────────────────────────────────────────────────────

def _log_trade(
    ticker: str,
    action: str,
    shares: int,
    price: float,
    stop_loss: float = 0,
    take_profit: float = 0,
    cost: float = 0,
    pnl: float = 0,
) -> None:
    trades = _load_json(STOCK_TRADES_FILE, [])
    trades.append({
        "ticker":      ticker,
        "action":      action,
        "shares":      shares,
        "price":       price,
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "cost":        cost,
        "pnl":         pnl,
        "mode":        "paper" if IS_PAPER else "live",
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })
    _save_json(STOCK_TRADES_FILE, trades)


def summarize_stock_trades() -> None:
    """Print a P&L summary — mirrors summarize_paper_trades()."""
    trades = _load_json(STOCK_TRADES_FILE, [])
    sells  = [t for t in trades if t["action"] == "SELL"]

    balance_data = _load_json(STOCK_BALANCE_FILE, {"balance": INITIAL_PAPER_BALANCE})
    balance      = balance_data.get("balance", INITIAL_PAPER_BALANCE)
    positions    = _load_json(STOCK_POSITIONS_FILE, {})

    print("\n" + "─" * 60)
    print(" STOCK PORTFOLIO SUMMARY ".center(60))
    print("─" * 60)
    print(f"  Mode:          {'PAPER' if IS_PAPER else '⚠ LIVE'}")
    print(f"  Cash balance:  ${balance:,.2f}")
    print(f"  Open positions:{len(positions)}")

    if positions:
        print("\n  Open Positions:")
        for ticker, pos in positions.items():
            entry = pos.get("entry_price", 0)
            shares = pos.get("shares", 0)
            sl   = pos.get("stop_loss", 0)
            tp   = pos.get("take_profit", 0)
            print(f"    {ticker}: {shares} shares @ ${entry:.2f} | SL: ${sl:.2f} | TP: ${tp:.2f}")

    if sells:
        total_pnl = sum(t.get("pnl", 0) for t in sells)
        wins  = sum(1 for t in sells if t.get("pnl", 0) > 0)
        losses = len(sells) - wins
        win_rate = (wins / len(sells) * 100) if sells else 0
        print(f"\n  Closed trades: {len(sells)}")
        print(f"  Win rate:      {win_rate:.1f}%  ({wins}W / {losses}L)")
        print(f"  Total P&L:     ${total_pnl:+,.2f}")
    else:
        print("\n  No closed trades yet.")

    print("─" * 60 + "\n")


def check_stock_positions_for_exits(fetch_fn) -> None:
    """
    Check open stock positions against current prices for SL/TP hits.
    Pass in fetch_stock_ohlcv as fetch_fn.
    Mirrors check_all_positions_for_exits() in main_enhanced.py.
    """
    positions = _load_json(STOCK_POSITIONS_FILE, {})
    if not positions:
        return

    from risk.dynamic_risk import load_enhanced_risk_config as load_config
    rc = load_config() or {}

    for ticker, position in list(positions.items()):
        try:
            df = fetch_fn(symbol=ticker, timeframe="5m", limit=1)
            if df is None or df.is_empty():
                continue

            current_price = float(df["close"][0])
            sl = position.get("stop_loss")
            tp = position.get("take_profit")
            currency = position.get("currency", "USD")

            if sl and current_price <= sl:
                logging.info(f"[alpaca_trader] {ticker}: Stop Loss hit @ ${current_price:.2f}")
                print(f"\n[AUTO EXIT] {ticker} Stop Loss hit @ ${current_price:.2f}")
                execute_stock_trade(ticker, -1, current_price, currency, override_risk=True)

            elif tp and current_price >= tp:
                logging.info(f"[alpaca_trader] {ticker}: Take Profit hit @ ${current_price:.2f}")
                print(f"\n[AUTO EXIT] {ticker} Take Profit hit @ ${current_price:.2f}")
                execute_stock_trade(ticker, -1, current_price, currency, override_risk=True)

        except Exception as e:
            logging.error(f"[alpaca_trader] Position check failed for {ticker}: {e}")