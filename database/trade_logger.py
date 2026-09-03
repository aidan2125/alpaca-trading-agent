#!/usr/bin/env python3
"""
database/trade_logger.py

SQLite trade logger — drop-in for main_enhanced.py
Stores every trade, signal change, heartbeat, agent decision, and daily
P&L summary.

Usage in main_enhanced.py:
    from database.trade_logger import TradeLogger
    logger = TradeLogger()                 # opens/creates trades.db
    logger.log_trade(...)                  # call after execute_paper_trade()
    logger.log_signal(...)                 # call after save_last_signals()
    logger.log_decision(...)               # call once per agent reasoning cycle
    logger.write_heartbeat()               # call at top of each main loop
    logger.daily_summary()                 # call at midnight or on /status
"""

import sqlite3
import fcntl
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Optional

LOG = logging.getLogger(__name__)

# ── Default DB path — override via env var ────────────────────────────────────
DEFAULT_DB_PATH = Path(os.getenv("TRADE_DB_PATH", "data/trades.db"))


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
-- Every executed trade (entry + exit)
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,               -- ISO-8601 UTC
    symbol          TEXT    NOT NULL,               -- e.g. BTC/USDT or AAPL
    asset_type      TEXT    NOT NULL DEFAULT 'crypto', -- 'crypto' | 'stock'
    direction       TEXT    NOT NULL,               -- 'LONG' | 'SHORT'
    action          TEXT    NOT NULL,               -- 'ENTRY' | 'EXIT'
    exit_reason     TEXT,                           -- 'TP' | 'SL' | 'SIGNAL' | 'END_OF_DATA'
    price           REAL    NOT NULL,
    quantity        REAL    NOT NULL,
    position_size   REAL,                           -- USD value
    stop_loss       REAL,
    take_profit     REAL,
    pnl             REAL,                           -- NULL on entry, set on exit
    pnl_pct         REAL,                           -- % return on position
    atr             REAL,
    signal_quality  REAL,
    strategy_mode   TEXT,                           -- 'strict' | 'relaxed' etc.
    run_id          INTEGER,                        -- links to runs table
    notes           TEXT                            -- free-form JSON blob
);

-- Signal changes (even when no trade is placed)
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    asset_type      TEXT    NOT NULL DEFAULT 'crypto',
    prev_signal     INTEGER,                        -- -1 | 0 | 1
    new_signal      INTEGER NOT NULL,
    price           REAL,
    atr             REAL,
    signal_quality  REAL,
    strategy_mode   TEXT,
    acted_on        INTEGER NOT NULL DEFAULT 0      -- 1 if a trade was placed
);

-- Agent reasoning-cycle decisions — one row per completed run_cycle().
-- Distinct from `trades` (only fills placed) and `signals` (only signal
-- flips): this is "what did the agent conclude this cycle", including
-- cycles where it looked at the market and decided not to act, or where
-- a risk gate blocked an attempted order. The dashboard's Decisions and
-- Trades/Blocked panels both read this table.
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,               -- ISO-8601 UTC
    symbol          TEXT,                           -- may be NULL for a whole-cycle summary
    decision        TEXT,                           -- e.g. tool/action name, or 'NO_ACTION'
    status          TEXT    NOT NULL DEFAULT 'INFO', -- 'INFO' | 'TRADED' | 'BLOCKED' | 'ERROR'
    confidence      REAL,                           -- 0-100, optional
    risk_level      TEXT,                           -- optional free-form label
    reasoning       TEXT,                           -- model's explanation, plain text
    raw             TEXT,                           -- optional JSON blob (full context)
    run_id          INTEGER
);

-- Bot run sessions (each time main() starts)
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    mode            TEXT,                           -- 'crypto' | 'stocks' | 'dual'
    run_count       INTEGER DEFAULT 0,
    notes           TEXT
);

-- Heartbeat (one row, updated in-place)
CREATE TABLE IF NOT EXISTS heartbeat (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    ts              TEXT    NOT NULL,
    run_id          INTEGER,
    loop_count      INTEGER DEFAULT 0
);

-- Pre-computed daily P&L summaries (cached so /status is fast)
CREATE TABLE IF NOT EXISTS daily_summary (
    summary_date    TEXT    PRIMARY KEY,            -- YYYY-MM-DD
    asset_type      TEXT    NOT NULL DEFAULT 'all',
    total_trades    INTEGER DEFAULT 0,
    wins            INTEGER DEFAULT 0,
    losses          INTEGER DEFAULT 0,
    gross_pnl       REAL    DEFAULT 0,
    win_rate        REAL    DEFAULT 0,
    profit_factor   REAL    DEFAULT 0,
    best_trade_pnl  REAL,
    worst_trade_pnl REAL,
    computed_at     TEXT    NOT NULL
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_trades_symbol   ON trades    (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_ts       ON trades    (ts);
CREATE INDEX IF NOT EXISTS idx_signals_symbol  ON signals   (symbol);
CREATE INDEX IF NOT EXISTS idx_signals_ts      ON signals   (ts);
CREATE INDEX IF NOT EXISTS idx_decisions_ts    ON decisions (ts);
"""


# ─────────────────────────────────────────────────────────────────────────────
# TradeLogger
# ─────────────────────────────────────────────────────────────────────────────

class TradeLogger:
    """
    Thread-safe SQLite trade logger.

    All writes use WAL mode + exclusive locks so the Telegram bot
    can read from the same file concurrently without corruption.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id: Optional[int] = None
        self._init_db()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        """Yield a connection in WAL mode; auto-commit or rollback."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        LOG.info(f"TradeLogger ready → {self.db_path}")

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # ── Run session ───────────────────────────────────────────────────────────

    def start_run(self, mode: str = "crypto", notes: str = "") -> int:
        """
        Call once at bot startup — creates a run session row.
        Returns the run_id; store it and pass to log_trade() if you want
        trades linked to their run session.

            self._run_id = logger.start_run(mode=TRADING_MODE)
        """
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, mode, notes) VALUES (?, ?, ?)",
                (self._now(), mode, notes)
            )
            self._run_id = cur.lastrowid
        LOG.info(f"Run session started: id={self._run_id}, mode={mode}")
        return self._run_id

    def end_run(self, run_count: int = 0):
        """Call on clean shutdown."""
        if self._run_id is None:
            return
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET ended_at=?, run_count=? WHERE id=?",
                (self._now(), run_count, self._run_id)
            )

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def write_heartbeat(self, loop_count: int = 0):
        """
        Call at the top of every main loop iteration.
        Upserts a single row — the Telegram bot's /status reads this
        to show "Online (last heartbeat 42s ago)".

        Also writes data/heartbeat.json for your existing Telegram bot
        (check_trading_bot_health() reads that file).
        """
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO heartbeat (id, ts, run_id, loop_count)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    ts=excluded.ts,
                    run_id=excluded.run_id,
                    loop_count=excluded.loop_count
                """,
                (now, self._run_id, loop_count)
            )

        # Keep the flat-file heartbeat.json in sync so your existing
        # Telegram bot (check_trading_bot_health) keeps working unchanged
        hb_path = self.db_path.parent / "heartbeat.json"
        try:
            with open(hb_path, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump({"timestamp": now, "loop_count": loop_count}, f)
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            LOG.warning(f"Could not write heartbeat.json: {e}")

    # ── Trade logging ─────────────────────────────────────────────────────────

    def log_trade(
        self,
        symbol: str,
        action: str,                    # 'ENTRY' | 'EXIT'
        direction: str,                 # 'LONG' | 'SHORT'
        price: float,
        quantity: float,
        asset_type: str = "crypto",
        position_size: float = None,
        stop_loss: float = None,
        take_profit: float = None,
        pnl: float = None,              # set on EXIT
        pnl_pct: float = None,
        exit_reason: str = None,        # 'TP' | 'SL' | 'SIGNAL'
        atr: float = None,
        signal_quality: float = None,
        strategy_mode: str = None,
        notes: dict = None,
    ) -> int:
        """
        Insert one trade row. Returns the new row id.

        Typical call after execute_paper_trade() returns:

            logger.log_trade(
                symbol       = coin,
                action       = "ENTRY",
                direction    = "LONG",
                price        = price,
                quantity     = trade_result["quantity"],
                position_size= trade_result["size_usd"],
                stop_loss    = trade_result["stop_loss"],
                take_profit  = trade_result["take_profit"],
                atr          = atr,
                signal_quality = signal_quality,
                strategy_mode= "strict",
            )
        """
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    ts, symbol, asset_type, direction, action, exit_reason,
                    price, quantity, position_size, stop_loss, take_profit,
                    pnl, pnl_pct, atr, signal_quality, strategy_mode, run_id, notes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self._now(), symbol, asset_type, direction, action, exit_reason,
                    price, quantity, position_size, stop_loss, take_profit,
                    pnl, pnl_pct, atr, signal_quality, strategy_mode,
                    self._run_id,
                    json.dumps(notes) if notes else None,
                )
            )
            row_id = cur.lastrowid

        LOG.info(
            f"trade logged | {symbol} {action} {direction} @ {price:.2f}"
            + (f" | P&L ${pnl:+.2f}" if pnl is not None else "")
        )

        # Keep trades.json in sync for your existing Telegram /trades command
        self._sync_trades_json()

        return row_id

    def _sync_trades_json(self, limit: int = 100):
        """
        Write the last `limit` closed trades back to data/trades.json
        so your existing Telegram bot /trades command keeps working unchanged.
        """
        trades_path = self.db_path.parent / "trades.json"
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol AS coin, action, direction,
                           price, quantity, pnl, exit_reason, ts AS timestamp
                    FROM   trades
                    WHERE  action = 'EXIT'
                    ORDER  BY id DESC
                    LIMIT  ?
                    """,
                    (limit,)
                ).fetchall()

            records = [dict(r) for r in rows]

            with open(trades_path, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                json.dump(records, f, indent=2)
                fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            LOG.warning(f"Could not sync trades.json: {e}")

    # ── Signal logging ────────────────────────────────────────────────────────

    def log_signal(
        self,
        symbol: str,
        new_signal: int,
        prev_signal: int = None,
        price: float = None,
        asset_type: str = "crypto",
        atr: float = None,
        signal_quality: float = None,
        strategy_mode: str = None,
        acted_on: bool = False,
    ) -> int:
        """
        Log every signal change — even ones that don't trigger a trade.
        Call this alongside save_last_signals() in run_for_coin/run_for_stock.
        """
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO signals (
                    ts, symbol, asset_type, prev_signal, new_signal,
                    price, atr, signal_quality, strategy_mode, acted_on
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    self._now(), symbol, asset_type, prev_signal, new_signal,
                    price, atr, signal_quality, strategy_mode, int(acted_on)
                )
            )
            return cur.lastrowid

    # ── Decision logging ──────────────────────────────────────────────────────

    def log_decision(
        self,
        decision: str = None,
        status: str = "INFO",          # 'INFO' | 'TRADED' | 'BLOCKED' | 'ERROR'
        symbol: str = None,
        confidence: float = None,
        risk_level: str = None,
        reasoning: str = None,
        raw: dict = None,
    ) -> int:
        """
        Log one agent reasoning-cycle outcome. Call once per run_cycle(),
        even on a no-op cycle (status='INFO', decision='NO_ACTION') — the
        dashboard's Decisions panel has nothing to show on quiet cycles
        otherwise. On a blocked order, pass status='BLOCKED' so it also
        surfaces in the dashboard's Trades panel (which filters decisions
        by status='BLOCKED' alongside real Alpaca orders).
        """
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO decisions (
                    ts, symbol, decision, status, confidence,
                    risk_level, reasoning, raw, run_id
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    self._now(), symbol, decision, status, confidence,
                    risk_level, reasoning,
                    json.dumps(raw, default=str) if raw else None,
                    self._run_id,
                )
            )
            row_id = cur.lastrowid
        LOG.info(f"decision logged | {symbol or '-'} {decision or '-'} [{status}]")
        return row_id

    def get_recent_decisions(self, limit: int = 30) -> list[dict]:
        """Return the most recent agent decisions as a list of dicts, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Queries (used by Telegram bot / daily report) ─────────────────────────

    def get_recent_trades(self, limit: int = 20, asset_type: str = None) -> list[dict]:
        """Return the most recent closed trades as a list of dicts."""
        with self._conn() as conn:
            query = "SELECT * FROM trades WHERE action='EXIT'"
            params: list = []
            if asset_type:
                query += " AND asset_type=?"
                params.append(asset_type)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_open_positions_summary(self) -> dict:
        """
        Return count + total cost basis of open positions from the DB.
        (Complements your existing positions.json.)
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt,
                       SUM(price * quantity) AS cost_basis
                FROM   trades
                WHERE  action = 'ENTRY'
                  AND  symbol NOT IN (
                      SELECT symbol FROM trades WHERE action='EXIT'
                  )
                """
            ).fetchone()
        return {"count": row["cnt"] or 0, "cost_basis": row["cost_basis"] or 0.0}

    def get_last_heartbeat(self) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM heartbeat WHERE id=1").fetchone()
        return dict(row) if row else None

    # ── Daily summary ─────────────────────────────────────────────────────────

    def daily_summary(
        self,
        for_date: date = None,
        asset_type: str = "all",
    ) -> dict:
        """
        Compute (and cache) a daily P&L summary.
        Call this at midnight or when the Telegram bot requests /status.

        Returns a dict with keys:
            total_trades, wins, losses, gross_pnl, win_rate,
            profit_factor, best_trade_pnl, worst_trade_pnl
        """
        target = (for_date or date.today()).isoformat()

        with self._conn() as conn:
            # Try cache first
            cached = conn.execute(
                "SELECT * FROM daily_summary WHERE summary_date=? AND asset_type=?",
                (target, asset_type)
            ).fetchone()
            if cached:
                return dict(cached)

            # Compute fresh
            q = """
                SELECT pnl FROM trades
                WHERE  action='EXIT'
                  AND  date(ts) = ?
                  AND  pnl IS NOT NULL
            """
            params: list = [target]
            if asset_type != "all":
                q += " AND asset_type=?"
                params.append(asset_type)

            rows = conn.execute(q, params).fetchall()
            pnls = [r["pnl"] for r in rows]

            if not pnls:
                return {}

            wins   = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            gross_profit = sum(wins)
            gross_loss   = abs(sum(losses))

            summary = {
                "summary_date":   target,
                "asset_type":     asset_type,
                "total_trades":   len(pnls),
                "wins":           len(wins),
                "losses":         len(losses),
                "gross_pnl":      sum(pnls),
                "win_rate":       len(wins) / len(pnls) if pnls else 0.0,
                "profit_factor":  gross_profit / gross_loss if gross_loss > 0 else 0.0,
                "best_trade_pnl": max(pnls),
                "worst_trade_pnl":min(pnls),
                "computed_at":    self._now(),
            }

            conn.execute(
                """
                INSERT OR REPLACE INTO daily_summary
                    (summary_date, asset_type, total_trades, wins, losses,
                     gross_pnl, win_rate, profit_factor,
                     best_trade_pnl, worst_trade_pnl, computed_at)
                VALUES
                    (:summary_date,:asset_type,:total_trades,:wins,:losses,
                     :gross_pnl,:win_rate,:profit_factor,
                     :best_trade_pnl,:worst_trade_pnl,:computed_at)
                """,
                summary,
            )

        return summary

    def format_daily_summary(self, for_date: date = None) -> str:
        """Return a Telegram-ready daily summary string."""
        s = self.daily_summary(for_date=for_date)
        if not s:
            return "No closed trades today."

        pnl_sign = "+" if s["gross_pnl"] >= 0 else ""
        return (
            f"Daily Summary — {s['summary_date']}\n\n"
            f"Trades:         {s['total_trades']}\n"
            f"Wins / Losses:  {s['wins']} / {s['losses']}\n"
            f"Win Rate:       {s['win_rate']*100:.1f}%\n"
            f"Profit Factor:  {s['profit_factor']:.2f}\n"
            f"Gross P&L:      ${pnl_sign}{s['gross_pnl']:.2f}\n"
            f"Best Trade:     ${s['best_trade_pnl']:+.2f}\n"
            f"Worst Trade:    ${s['worst_trade_pnl']:+.2f}\n"
        )