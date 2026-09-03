"""
risk/agent_safety_gate.py

Central safety gate for AI-generated Alpaca orders.

IMPORTANT:
This gate runs BEFORE any trading MCP tool is executed.

The LLM can request an order, but Python decides whether
that order is allowed to reach Alpaca.
"""

import logging
from datetime import datetime, timezone

from risk.kill_switch import check_kill_switch

logger = logging.getLogger(__name__)


# Tools that can potentially modify trading/account state.
TRADING_TOOLS = {
    "place_stock_order",
    "place_crypto_order",
    "place_option_order",
    "replace_order_by_id",
    "cancel_order_by_id",
    "cancel_all_orders",
    "close_position",
    "close_all_positions",
}


def _validate_positive_number(
    value,
    field_name: str,
) -> tuple[bool, str]:
    """Validate that a value is a positive number."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return False, f"{field_name} must be numeric"

    if number <= 0:
        return False, f"{field_name} must be greater than zero"

    return True, ""


def validate_order_arguments(
    tool_name: str,
    arguments: dict,
) -> tuple[bool, str]:
    """
    Validate the basic structure of an AI-generated order request.

    This function does NOT execute anything.
    """

    if not isinstance(arguments, dict):
        return False, "Order arguments must be an object"

    # ---------------------------------------------------------
    # STOCK ORDERS
    # ---------------------------------------------------------

    if tool_name == "place_stock_order":

        symbol = arguments.get("symbol")
        side = arguments.get("side")
        qty = arguments.get("qty")
        notional = arguments.get("notional")

        # Required fields
        if not symbol:
            return False, "Missing required field: symbol"

        if not isinstance(symbol, str):
            return False, "symbol must be a string"

        symbol = symbol.strip().upper()

        if not symbol.isalnum():
            return False, f"Invalid stock symbol: {symbol}"

        if side not in {"buy", "sell"}:
            return False, "side must be 'buy' or 'sell'"

        # -----------------------------------------------------
        # qty / notional
        # -----------------------------------------------------

        if qty is not None and notional is not None:
            return False, (
                "qty and notional cannot both be provided"
            )

        if qty is None and notional is None:
            return False, (
                "Either qty or notional must be provided"
            )

        if qty is not None:
            valid, reason = _validate_positive_number(
                qty,
                "qty",
            )

            if not valid:
                return False, reason

        if notional is not None:
            valid, reason = _validate_positive_number(
                notional,
                "notional",
            )

            if not valid:
                return False, reason

        # -----------------------------------------------------
        # Initial integration only allows market + day orders.
        # -----------------------------------------------------

        order_type = arguments.get(
            "type",
            "market",
        )

        if order_type != "market":
            return False, (
                f"Order type '{order_type}' is not enabled yet. "
                "Only market orders are allowed."
            )

        time_in_force = arguments.get(
            "time_in_force",
            "day",
        )

        if time_in_force != "day":
            return False, (
                f"time_in_force '{time_in_force}' is not enabled yet. "
                "Only day orders are allowed."
            )

        return True, (
            f"Basic order validation passed: "
            f"{side.upper()} {symbol}"
        )

    # ---------------------------------------------------------
    # Other trading tools are NOT yet safe to execute.
    # ---------------------------------------------------------

    return False, (
        f"Trading tool '{tool_name}' does not yet have "
        "a dedicated safety schema."
    )


def _get_max_position_value(
    account_value: float,
    settings,
) -> float:
    """
    Calculate the maximum allowed position value.

    Uses the conservative MAX_POSITION_PCT setting.
    """

    max_position_pct = getattr(
        settings,
        "MAX_POSITION_PCT",
        0.10,
    )

    return account_value * max_position_pct


def _get_max_trade_risk(
    account_value: float,
    settings,
) -> float:
    """
    Calculate the maximum allowed risk for one trade.

    Uses MAX_RISK_PER_TRADE from central settings.
    """

    max_risk_pct = getattr(
        settings,
        "MAX_RISK_PER_TRADE",
        0.01,
    )

    return account_value * max_risk_pct


def check_agent_order_safety(
    tool_name: str,
    arguments: dict,
    current_positions: int,
    settings,
    account_value: float | None = None,
) -> tuple[bool, str]:
    """
    Main safety gate.

    Returns:
        (allowed, reason)

    IMPORTANT:
    This function does NOT execute the order.

    The caller must only call the actual MCP trading tool
    if this function returns allowed=True.
    """

    logger.info(
        "SAFETY GATE: evaluating %s with arguments=%s",
        tool_name,
        arguments,
    )

    # ---------------------------------------------------------
    # 1. Is this a trading tool?
    # ---------------------------------------------------------

    if tool_name not in TRADING_TOOLS:
        return True, "Non-trading tool"

    # ---------------------------------------------------------
    # 2. Kill switch
    # ---------------------------------------------------------

    can_trade, reason = check_kill_switch()

    if not can_trade:
        logger.warning(
            "SAFETY BLOCK: %s",
            reason,
        )

        return False, reason

    # ---------------------------------------------------------
    # 3. Basic order validation
    # ---------------------------------------------------------

    valid, reason = validate_order_arguments(
        tool_name,
        arguments,
    )

    if not valid:
        logger.warning(
            "SAFETY BLOCK: %s",
            reason,
        )

        return False, reason

    # ---------------------------------------------------------
    # 4. Paper trading enforcement
    # ---------------------------------------------------------

    trading_mode = getattr(
        settings,
        "TRADING_MODE",
        "paper",
    )

    if str(trading_mode).lower() != "paper":

        reason = (
            "Trading blocked: agent safety gate requires "
            "PAPER trading mode."
        )

        logger.warning(
            "SAFETY BLOCK: %s",
            reason,
        )

        return False, reason

    # ---------------------------------------------------------
    # 5. Validate current position count
    # ---------------------------------------------------------

    if current_positions < 0:

        reason = (
            "Safety check failed: current position count "
            "could not be determined."
        )

        logger.warning(
            "SAFETY BLOCK: %s",
            reason,
        )

        return False, reason

    # ---------------------------------------------------------
    # 6. Maximum number of positions
    # ---------------------------------------------------------

    max_positions = getattr(
        settings,
        "MAX_POSITIONS",
        3,
    )

    if tool_name == "place_stock_order":

        side = arguments.get("side")

        # A BUY potentially opens a new position.
        if side == "buy":

            if current_positions >= max_positions:

                reason = (
                    f"Maximum positions reached: "
                    f"{current_positions}/{max_positions}"
                )

                logger.warning(
                    "SAFETY BLOCK: %s",
                    reason,
                )

                return False, reason

    # ---------------------------------------------------------
    # 7. Account value validation
    # ---------------------------------------------------------

    if account_value is not None:

        try:
            account_value = float(account_value)
        except (TypeError, ValueError):

            reason = (
                "Safety check failed: invalid account value."
            )

            logger.warning(
                "SAFETY BLOCK: %s",
                reason,
            )

            return False, reason

        if account_value <= 0:

            reason = (
                "Safety check failed: account value must "
                "be greater than zero."
            )

            logger.warning(
                "SAFETY BLOCK: %s",
                reason,
            )

            return False, reason

        # -----------------------------------------------------
        # 8. Maximum position value
        # -----------------------------------------------------

        if tool_name == "place_stock_order":

            notional = arguments.get("notional")
            qty = arguments.get("qty")

            # We can directly validate notional.
            if notional is not None:

                try:
                    requested_value = float(notional)
                except (TypeError, ValueError):

                    return False, (
                        "Safety check failed: invalid "
                        "notional value."
                    )

                max_position_value = _get_max_position_value(
                    account_value,
                    settings,
                )

                if requested_value > max_position_value:

                    reason = (
                        f"Position size exceeds limit: "
                        f"${requested_value:,.2f} > "
                        f"${max_position_value:,.2f}"
                    )

                    logger.warning(
                        "SAFETY BLOCK: %s",
                        reason,
                    )

                    return False, reason

            # -------------------------------------------------
            # qty cannot be converted to dollar value without
            # a live price. The order therefore remains subject
            # to the dry-run lock until price-aware sizing is
            # implemented.
            # -------------------------------------------------

            if qty is not None:

                logger.info(
                    "BUY/SELL qty order requires live price "
                    "for dollar-risk validation."
                )

    # ---------------------------------------------------------
    # 9. Record calculated risk limits in logs.
    # ---------------------------------------------------------

    if account_value is not None:

        max_trade_risk = _get_max_trade_risk(
            account_value,
            settings,
        )

        max_position_value = _get_max_position_value(
            account_value,
            settings,
        )

        logger.info(
            "Risk limits: max_trade_risk=$%.2f, "
            "max_position_value=$%.2f",
            max_trade_risk,
            max_position_value,
        )

    # ---------------------------------------------------------
    # 10. Explicit dry-run lock
    # ---------------------------------------------------------

    # IMPORTANT:
    # Keep this False until every safety component has been
    # fully tested.
    EXECUTION_ENABLED = False

    if not EXECUTION_ENABLED:

        reason = (
            "DRY RUN: safety checks passed, "
            "but actual order execution is disabled."
        )

        logger.info(
            "SAFETY HOLD: %s",
            reason,
        )

        return False, reason

    # ---------------------------------------------------------
    # ALL CHECKS PASSED
    # ---------------------------------------------------------

    logger.info(
        "SAFETY PASS: %s is approved for execution",
        tool_name,
    )

    return True, "All safety checks passed""""
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