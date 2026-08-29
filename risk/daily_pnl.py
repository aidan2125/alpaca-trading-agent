import os
import sqlite3
from datetime import datetime, timezone

# Must match database/trade_logger.py's DEFAULT_DB_PATH so both modules
# always read/write the same SQLite file regardless of where the process
# is launched from.
TRADES_DB = os.getenv("TRADE_DB_PATH", "data/trades.db")


def get_daily_realized_pnl(db_path=TRADES_DB, symbol=None):
    """
    Sum of realized PnL from EXIT trades for today (UTC).
    Opens the DB read-only so it never contends with the live bot's write lock.
    Returns 0.0 if there are no exits yet today.

    Args:
        db_path: path to the trades database
        symbol: if provided, restrict the sum to this symbol only (e.g. "AAPL").
                 If None, sums across all symbols (account-wide).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.cursor()
        if symbol is not None:
            cur.execute(
                """
                SELECT COALESCE(SUM(pnl), 0.0)
                FROM trades
                WHERE action = 'EXIT' AND date(ts) = ? AND symbol = ?
                """,
                (today, symbol)
            )
        else:
            cur.execute(
                """
                SELECT COALESCE(SUM(pnl), 0.0)
                FROM trades
                WHERE action = 'EXIT' AND date(ts) = ?
                """,
                (today,)
            )
        return cur.fetchone()[0]
    finally:
        conn.close()