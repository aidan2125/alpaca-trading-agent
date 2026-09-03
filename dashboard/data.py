"""
dashboard/data.py — Presentation-layer data access for the LIVE DASHBOARD.

Design rule: this module never talks to Alpaca, the database, or the risk
engine using new/duplicated logic. It only calls the existing:

    execution.alpaca_trader._get_client()   — the same Alpaca client the
                                               execution layer already uses
    database.trade_logger.TradeLogger       — the same SQLite log the agent
                                               and Telegram bot already use
    risk.*                                  — the existing risk checks, run
                                               here in *read-only* / status
                                               mode (never invoked with a
                                               constructed fake order)
    config.settings                         — the same environment-derived
                                               settings the agent uses

Nothing here can place, modify, or cancel an order. There is exactly one
order-placement path in this project (Alpaca's MCP server, gated by
risk/agent_safety_gate.py) and this module does not touch it.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from database.trade_logger import TradeLogger
from execution.alpaca_trader import _get_client, ALPACA_AVAILABLE
from risk.kill_switch import check_kill_switch, KILL_SWITCH_FILE
from risk.drawdown_guard import check_drawdown_limit
from risk.daily_pnl import get_daily_realized_pnl
from risk.agent_safety_gate import EXECUTION_ENABLED, TRADING_TOOLS

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
HEARTBEAT_LOG = ROOT / "logs" / "heartbeat.log"
BOT_LOG = ROOT / "logs" / "bot.log"

# A tiny in-process TTL cache so a handful of browser tabs polling every
# few seconds doesn't turn into a hammer against the Alpaca REST API.
_CACHE_TTL_SECONDS = 3
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def _trade_logger() -> TradeLogger:
    return TradeLogger()


def _redact(msg: str) -> str:
    """Defense in depth: strip anything that looks like a key/secret if it
    ever ended up in a log line or error string before it reaches the API."""
    if not msg:
        return msg
    lowered = msg.lower()
    for marker in ("api_key", "secret", "token", "password"):
        if marker in lowered:
            return "[redacted: message referenced a credential]"
    return msg


# ─────────────────────────────────────────────────────────────────────────
# Alpaca account snapshot (shared by portfolio/pnl/positions)
# ─────────────────────────────────────────────────────────────────────────

def _fetch_account() -> dict | None:
    client = _get_client()
    if client is None:
        return None
    try:
        account = client.get_account()
        return {
            "equity": float(account.equity),
            "last_equity": float(account.last_equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "portfolio_value": float(account.portfolio_value),
            "status": str(account.status),
            "pattern_day_trader": bool(account.pattern_day_trader),
        }
    except Exception as e:
        logger.warning(f"[dashboard] Alpaca get_account failed: {_redact(str(e))}")
        return None


def _fetch_positions() -> list[dict] | None:
    client = _get_client()
    if client is None:
        return None
    try:
        positions = client.get_all_positions()
        out = []
        for p in positions:
            entry = float(p.avg_entry_price)
            current = float(p.current_price) if p.current_price is not None else entry
            qty = float(p.qty)
            market_value = float(p.market_value) if p.market_value is not None else current * qty
            unrealized_pl = float(p.unrealized_pl) if p.unrealized_pl is not None else 0.0
            unrealized_plpc = float(p.unrealized_plpc) if p.unrealized_plpc is not None else 0.0
            out.append({
                "symbol": p.symbol,
                "asset_class": str(p.asset_class),
                "side": str(p.side).upper(),
                "qty": qty,
                "avg_entry_price": entry,
                "current_price": current,
                "market_value": market_value,
                "unrealized_pl": unrealized_pl,
                "unrealized_plpc": unrealized_plpc * 100,
            })
        return out
    except Exception as e:
        logger.warning(f"[dashboard] Alpaca get_all_positions failed: {_redact(str(e))}")
        return None


def _fetch_orders() -> list[dict] | None:
    if not ALPACA_AVAILABLE:
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=50, nested=False)
        orders = client.get_orders(req)
        out = []
        for o in orders:
            out.append({
                "id": str(o.id),
                "ts": (o.submitted_at or o.created_at).isoformat() if (o.submitted_at or o.created_at) else None,
                "symbol": o.symbol,
                "side": str(o.side).upper() if o.side else None,
                "qty": float(o.qty) if o.qty else None,
                "order_type": str(o.order_type) if o.order_type else None,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "status": str(o.status).upper() if o.status else "UNKNOWN",
                "source": "alpaca",
            })
        out.sort(key=lambda t: t["ts"] or "", reverse=True)
        return out
    except Exception as e:
        logger.warning(f"[dashboard] Alpaca get_orders failed: {_redact(str(e))}")
        return None


# ─────────────────────────────────────────────────────────────────────────
# Public: PORTFOLIO
# ─────────────────────────────────────────────────────────────────────────

def get_portfolio() -> dict:
    account = _cached("account", _fetch_account)
    positions = _cached("positions", _fetch_positions)

    is_paper = settings.ALPACA_PAPER
    connected = account is not None

    invested = None
    exposure_pct = None
    num_positions = None

    if positions is not None:
        num_positions = len(positions)
        invested = sum(p["market_value"] for p in positions)

    if account is not None and invested is not None and account["portfolio_value"] > 0:
        exposure_pct = (invested / account["portfolio_value"]) * 100

    return {
        "connected": connected,
        "mode": "PAPER" if is_paper else "LIVE",
        "is_paper": is_paper,
        "equity": account["equity"] if account else None,
        "cash": account["cash"] if account else None,
        "buying_power": account["buying_power"] if account else None,
        "invested_value": invested,
        "exposure_pct": exposure_pct,
        "num_positions": num_positions,
        "account_status": account["status"] if account else "UNAVAILABLE",
    }


# ─────────────────────────────────────────────────────────────────────────
# Public: P&L
# ─────────────────────────────────────────────────────────────────────────

def get_pnl() -> dict:
    account = _cached("account", _fetch_account)
    tl = _trade_logger()

    today_pl = None
    today_pl_pct = None
    if account is not None:
        today_pl = account["equity"] - account["last_equity"]
        if account["last_equity"]:
            today_pl_pct = (today_pl / account["last_equity"]) * 100

    try:
        realized_today = get_daily_realized_pnl(db_path=tl.db_path)
    except Exception as e:
        logger.warning(f"[dashboard] get_daily_realized_pnl failed: {e}")
        realized_today = None

    positions = _cached("positions", _fetch_positions)
    unrealized_total = (
        sum(p["unrealized_pl"] for p in positions) if positions is not None else None
    )

    summary = {}
    try:
        summary = tl.daily_summary() or {}
    except Exception as e:
        logger.warning(f"[dashboard] daily_summary failed: {e}")

    return {
        "today_pl": today_pl,
        "today_pl_pct": today_pl_pct,
        "realized_pl_today": realized_today,
        "unrealized_pl_total": unrealized_total,
        "closed_trades_today": summary.get("total_trades"),
        "win_rate": summary.get("win_rate"),
        "history_available": False,  # no time-series equity history is persisted anywhere yet
    }


# ─────────────────────────────────────────────────────────────────────────
# Public: POSITIONS
# ─────────────────────────────────────────────────────────────────────────

def get_positions() -> dict:
    positions = _cached("positions", _fetch_positions)
    connected = _cached("account", _fetch_account) is not None
    return {
        "connected": connected,
        "positions": positions or [],
    }


# ─────────────────────────────────────────────────────────────────────────
# Public: TRADES / ORDERS
# ─────────────────────────────────────────────────────────────────────────

def get_trades(limit: int = 30) -> dict:
    orders = _cached("orders", _fetch_orders)
    connected = orders is not None

    # Surface risk-gate blocks alongside real orders so a blocked attempt
    # is visible in the same feed, per spec ("If an order was blocked by a
    # risk gate, that should be visible").
    tl = _trade_logger()
    try:
        blocked = [
            d for d in tl.get_recent_decisions(limit=limit)
            if d.get("status") == "BLOCKED" and d.get("decision") in TRADING_TOOLS
        ]
    except Exception as e:
        logger.warning(f"[dashboard] get_recent_decisions (blocked) failed: {e}")
        blocked = []

    blocked_rows = [
        {
            "id": f"blocked-{b['id']}",
            "ts": b["ts"],
            "symbol": b.get("symbol"),
            "side": None,
            "qty": None,
            "order_type": None,
            "limit_price": None,
            "filled_avg_price": None,
            "status": "BLOCKED",
            "source": "risk_gate",
            "reason": b.get("reasoning"),
        }
        for b in blocked
    ]

    combined = (orders or []) + blocked_rows
    combined.sort(key=lambda t: t["ts"] or "", reverse=True)

    return {
        "connected": connected,
        "trades": combined[:limit],
    }


# ─────────────────────────────────────────────────────────────────────────
# Public: AI DECISIONS
# ─────────────────────────────────────────────────────────────────────────

def get_decisions(limit: int = 30) -> dict:
    tl = _trade_logger()
    try:
        rows = tl.get_recent_decisions(limit=limit)
    except Exception as e:
        logger.warning(f"[dashboard] get_recent_decisions failed: {e}")
        rows = []

    decisions = []
    for r in rows:
        raw = r.get("raw")
        try:
            raw_parsed = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            raw_parsed = raw
        decisions.append({
            "id": r["id"],
            "ts": r["ts"],
            "symbol": r.get("symbol"),
            "decision": r.get("decision"),
            "status": r.get("status"),
            "confidence": r.get("confidence"),
            "risk_level": r.get("risk_level"),
            "reasoning": r.get("reasoning") or "",
            "raw": raw_parsed,
        })

    return {"decisions": decisions}


# ─────────────────────────────────────────────────────────────────────────
# Public: RISK GATES  (status only — never constructs or evaluates a real
# order; each check reflects the *actual current* state of the system)
# ─────────────────────────────────────────────────────────────────────────

def get_risk_status() -> dict:
    gates = []

    # Kill switch
    can_trade, reason = check_kill_switch()
    gates.append({
        "name": "Kill Switch",
        "status": "PASS" if can_trade else "BLOCKED",
        "detail": reason,
    })

    # Trading mode enforcement (agent safety gate hard-requires paper)
    trading_mode = str(settings.TRADING_MODE).lower()
    gates.append({
        "name": "Trading Mode",
        "status": "PASS" if trading_mode == "paper" else "WARNING",
        "detail": f"Configured mode: {trading_mode.upper()}"
                  + ("" if trading_mode == "paper" else " — safety gate blocks non-paper orders"),
    })

    # Execution dry-run lock (risk/agent_safety_gate.py EXECUTION_ENABLED)
    gates.append({
        "name": "Execution Lock",
        "status": "WARNING" if not EXECUTION_ENABLED else "PASS",
        "detail": (
            "DRY RUN — safety checks may pass but no order is ever sent to Alpaca"
            if not EXECUTION_ENABLED
            else "Execution enabled — approved orders reach Alpaca"
        ),
    })

    # Daily loss limit
    account = _cached("account", _fetch_account)
    tl = _trade_logger()
    try:
        realized_today = get_daily_realized_pnl(db_path=tl.db_path)
    except Exception:
        realized_today = None

    if account is not None and realized_today is not None:
        limit_dollars = account["equity"] * settings.DAILY_LOSS_LIMIT
        loss_today = abs(min(realized_today, 0))
        status = "BLOCKED" if limit_dollars > 0 and loss_today >= limit_dollars else "PASS"
        gates.append({
            "name": "Daily Loss",
            "status": status,
            "detail": f"${loss_today:,.2f} lost today vs ${limit_dollars:,.2f} limit "
                      f"({settings.DAILY_LOSS_LIMIT * 100:.0f}% of equity)",
        })
    else:
        gates.append({
            "name": "Daily Loss",
            "status": "UNKNOWN",
            "detail": "Account equity or realized P&L unavailable",
        })

    # Max positions
    positions = _cached("positions", _fetch_positions)
    if positions is not None:
        count = len(positions)
        status = "BLOCKED" if count >= settings.MAX_POSITIONS else "PASS"
        gates.append({
            "name": "Max Positions",
            "status": status,
            "detail": f"{count}/{settings.MAX_POSITIONS} open positions",
        })
    else:
        gates.append({
            "name": "Max Positions",
            "status": "UNKNOWN",
            "detail": "Position count unavailable (Alpaca not connected)",
        })

    # Max position size (configured limit — informational; evaluated for
    # real at order time by risk/agent_safety_gate.py, never re-derived here)
    gates.append({
        "name": "Max Position Size",
        "status": "PASS",
        "detail": f"Configured limit: {settings.MAX_POSITION_PCT * 100:.0f}% of equity per position",
    })

    # Order validation (schema-level — market/day orders only)
    gates.append({
        "name": "Order Validation",
        "status": "PASS",
        "detail": "Only market orders with time_in_force=day are accepted",
    })

    # Duplicate order / position-count fail-closed check (agent/loop.py)
    gates.append({
        "name": "Duplicate Order Protection",
        "status": "PASS",
        "detail": "Trades fail closed if current Alpaca position count can't be confirmed",
    })

    return {"gates": gates}


# ─────────────────────────────────────────────────────────────────────────
# Public: AGENT STATUS
# ─────────────────────────────────────────────────────────────────────────

def _tail_last_line(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, 4096)
            f.seek(size - block)
            lines = f.read().decode(errors="ignore").splitlines()
        return lines[-1] if lines else None
    except OSError:
        return None


def get_agent_status() -> dict:
    tl = _trade_logger()

    # Heartbeat freshness -> is the agent loop actually alive?
    last_heartbeat_line = _tail_last_line(HEARTBEAT_LOG)
    heartbeat_ts = None
    if last_heartbeat_line and " | HEARTBEAT" in last_heartbeat_line:
        heartbeat_ts = last_heartbeat_line.split(" | ")[0]

    agent_running = False
    if heartbeat_ts:
        try:
            ts = datetime.fromisoformat(heartbeat_ts)
            age = (datetime.now() - ts).total_seconds()
            # heartbeat.py writes every 300s — call it stale after 2x that
            agent_running = age < (2 * 300)
        except ValueError:
            agent_running = False

    can_trade, kill_switch_reason = check_kill_switch()

    account = _cached("account", _fetch_account)
    alpaca_connected = account is not None

    try:
        decisions = tl.get_recent_decisions(limit=1)
        last_decision_ts = decisions[0]["ts"] if decisions else None
    except Exception:
        last_decision_ts = None

    try:
        trades = tl.get_recent_trades(limit=1)
        last_trade_ts = trades[0]["ts"] if trades else None
    except Exception:
        last_trade_ts = None

    db_ok = True
    try:
        tl.get_last_heartbeat()
    except Exception:
        db_ok = False

    return {
        "agent_running": agent_running,
        "alpaca_connected": alpaca_connected,
        "database_connected": db_ok,
        "risk_engine_active": True,  # the safety gate is always imported/enforced in-process
        "execution_engine_ready": ALPACA_AVAILABLE,
        "trading_mode": settings.TRADING_MODE,
        "kill_switch_active": not can_trade,
        "kill_switch_reason": kill_switch_reason,
        "last_heartbeat": heartbeat_ts,
        "last_analysis": last_decision_ts,
        "last_decision": last_decision_ts,
        "last_trade": last_trade_ts,
        "bot_interval_seconds": settings.BOT_INTERVAL_SECONDS,
    }


def get_health() -> dict:
    """Cheap liveness check for GET /api/health — no external calls."""
    return {
        "ok": True,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER" if settings.ALPACA_PAPER else "LIVE",
    }
