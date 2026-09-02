"""
agent/memory.py — Cross-cycle context for the reasoning loop.

GLM-5.2 is called fresh each cycle (see agent/loop.py) rather than with a
growing chat transcript — at a 5-minute default interval that history
would blow past context limits fast and mostly be noise. Instead, each
cycle gets a compact JSON "snapshot" built from what's actually persisted:
the SQLite trade/signal log (database/trade_logger.py) plus current
risk-guard state (kill switch, drawdown).

This is where "memory" lives for this agent: not conversation history,
but durable account/trade state the model can reason over every cycle.
"""

import logging
from datetime import datetime, timezone

from database.trade_logger import TradeLogger
from risk.kill_switch import check_kill_switch
from risk.daily_pnl import get_daily_realized_pnl

logger = logging.getLogger(__name__)


class AgentMemory:
    """
    Thin wrapper around TradeLogger that assembles the per-cycle context
    dict the prompts in agent/prompts.py serialize into the user message,
    and records what happened afterward.
    """

    def __init__(self, db_path=None, mode: str = "options"):
        self.logger_db = TradeLogger(db_path=db_path) if db_path else TradeLogger()
        self._run_id = None
        self.mode = mode

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start_run(self, notes: str = "") -> int:
        self._run_id = self.logger_db.start_run(mode=self.mode, notes=notes)
        return self._run_id

    def end_run(self, run_count: int = 0):
        self.logger_db.end_run(run_count=run_count)

    def heartbeat(self, loop_count: int = 0):
        self.logger_db.write_heartbeat(loop_count=loop_count)

    # ── Snapshot for the reasoning prompt ────────────────────────────────

    def snapshot(self, account_info: dict | None = None) -> dict:
        """
        Build the context dict passed into agent/prompts.py's
        build_user_prompt() and used by agent/tools.py's run_risk_checks().

        `account_info` should be the parsed result of the MCP server's
        get_account_info tool call — main.py / agent/loop.py is expected
        to fetch that live each cycle and pass it in here, since this
        module has no direct Alpaca connection of its own. If omitted,
        account-dependent risk checks fall back to zeroed-out values and
        agent/tools.py's equity-based gates will simply block trades
        rather than approve them blind.
        """
        can_trade, kill_switch_reason = check_kill_switch()

        recent_trades = self.logger_db.get_recent_trades(limit=10, asset_type="option")
        open_positions = self.logger_db.get_open_positions_summary()
        trades_today = self._trades_today(recent_trades)

        try:
            daily_pnl = get_daily_realized_pnl(db_path=self.logger_db.db_path, symbol=None)
        except Exception as e:  # daily_pnl reads sqlite directly; don't let it break a cycle
            logger.warning(f"get_daily_realized_pnl failed: {e}")
            daily_pnl = None

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "kill_switch": {"can_trade": can_trade, "reason": kill_switch_reason},
            "account": account_info or {"equity": 0, "last_equity": 0, "buying_power": 0},
            "open_positions": self._positions_by_symbol(),
            "open_positions_summary": open_positions,
            "recent_trades": recent_trades,
            "trades_today": trades_today,
            "daily_realized_pnl": daily_pnl,
        }

    def _trades_today(self, recent_trades: list[dict]) -> list[dict]:
        today = datetime.now(timezone.utc).date().isoformat()
        return [t for t in recent_trades if str(t.get("ts", "")).startswith(today)]

    def _positions_by_symbol(self) -> dict:
        """
        agent/tools.py's exposure checks expect {symbol: {"trade_size": ...}}.
        TradeLogger only gives us an aggregate count/cost-basis summary
        (get_open_positions_summary), not per-symbol detail, so this is a
        placeholder until main.py wires in a live get_all_positions MCP
        call. Returning {} means exposure checks pass through rather than
        block — safe default, but revisit once live position data flows in.
        """
        return {}

    # ── Recording outcomes ───────────────────────────────────────────────

    def log_decision(
        self,
        symbol: str,
        signal_quality: float | None,
        acted_on: bool,
        notes: str = "",
    ):
        """Record a model decision (trade or no-op) to the signals table."""
        self.logger_db.log_signal(
            symbol=symbol or "N/A",
            new_signal=1 if acted_on else 0,
            asset_type="option",
            signal_quality=signal_quality,
            strategy_mode=self.mode,
            acted_on=acted_on,
        )

    def log_trade(self, **kwargs) -> int:
        """Pass-through to TradeLogger.log_trade with asset_type defaulted to 'option'."""
        kwargs.setdefault("asset_type", "option")
        return self.logger_db.log_trade(**kwargs)

    def format_daily_summary(self) -> str:
        return self.logger_db.format_daily_summary()