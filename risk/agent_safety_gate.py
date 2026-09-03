"""
risk/agent_safety_gate.py — single choke point for order-placing MCP calls.

Two things live here:

  EXECUTION_ENABLED
      Whether an approved order is actually allowed to reach Alpaca.
      Sourced from settings (env-var-backed, same pattern as the rest of
      this project) rather than invented here. Defaults to False — a
      missing or misconfigured env var fails CLOSED into dry-run, never
      into live trading.

  TRADING_TOOLS
      The set of Alpaca MCP tool names that place, modify, cancel, or
      otherwise change order/position state. Enumerated by hand from the
      live tool list (26 tools total on the Alpaca MCP server as of
      2026-09-03) — NOT inferred from a naming convention, since guessing
      wrong here either silently gates too little (a real order slips
      through in dry-run) or too much (a harmless read gets blocked).
      Everything read-only (get_*, list_*, search_*, fetch_*) and
      update_account_config (account settings, not order flow) is
      deliberately excluded.

enforce_gate() is the actual enforcement logic: agent/loop.py calls this
before dispatching any MCP tool call. This is intentionally the ONLY
place EXECUTION_ENABLED is checked — the trading tools themselves have no
independent dry-run awareness, so if this function isn't called before a
TRADING_TOOLS name is dispatched, the gate does nothing. Don't add a
second check site; extend this one instead.
"""

import logging

from config import settings

logger = logging.getLogger(__name__)

# Fails closed: dry-run unless settings explicitly say otherwise.
EXECUTION_ENABLED: bool = bool(getattr(settings, "EXECUTION_ENABLED", False))

# Order-placing / order-modifying / position-changing tools on the Alpaca
# MCP server. Everything else that server exposes is read-only or account
# config and is intentionally NOT in this set.
TRADING_TOOLS: frozenset[str] = frozenset({
    "place_stock_order",
    "place_crypto_order",
    "place_option_order",
    "cancel_all_orders",
    "cancel_order_by_id",
    "replace_order_by_id",
    "close_all_positions",
    "close_position",
    "do_not_exercise_options_position",
    "exercise_options_position",
})


def is_trading_tool(name: str) -> bool:
    return name in TRADING_TOOLS


def enforce_gate(name: str, args: dict) -> dict | None:
    """
    Call before dispatching any MCP tool call. Returns None if the call
    should proceed normally (either it's not a trading tool, or execution
    is enabled). Returns a dict if the call should be BLOCKED instead of
    dispatched — the caller should use that dict as the tool result
    (e.g. json.dumps'd into the role:"tool" message) rather than actually
    invoking the tool, and should log it as a BLOCKED decision.
    """
    if not is_trading_tool(name):
        return None

    if EXECUTION_ENABLED:
        return None

    logger.warning(f"[safety_gate] Blocked {name}{args} — EXECUTION_ENABLED is False (dry run)")
    return {
        "blocked": True,
        "reason": (
            f"Execution is disabled (dry run mode) — {name} was not sent to Alpaca. "
            "Set EXECUTION_ENABLED=true in the environment to allow real order placement."
        ),
        "tool": name,
        "attempted_args": args,
    }