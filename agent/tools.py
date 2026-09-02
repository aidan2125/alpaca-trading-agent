"""
agent/tools.py — Executes model-chosen tool calls against the Alpaca MCP
server, with a hard risk-gate in front of anything that can move money.

The reasoning loop (agent/loop.py) decides *what* to call by reading text
back from GLM-5.2; this module is where we stop trusting the model and
verify a proposed trade against config/settings.py + risk/*.py before it
is allowed to reach Alpaca.

Nothing here talks to GLM/Featherless — this is purely the execution side.
"""

import json
import logging
from datetime import datetime, timezone

from risk.kill_switch import check_kill_switch
from risk.drawdown_guard import check_drawdown_limit
from risk.exposure_limiter import check_single_asset_exposure
from execution.execution_guard import check_execution_timing

logger = logging.getLogger(__name__)


# Tools that place, modify, or cancel real orders / positions — anything in
# this set is routed through run_risk_checks() before execution. Everything
# else (get_*, list_*, etc.) is treated as read-only and passed straight
# through to the MCP server.
TRADE_TOOL_NAMES = {
    "place_stock_order",
    "place_crypto_order",
    "place_option_order",
    "replace_order_by_id",
    "cancel_order_by_id",
    "cancel_all_orders",
    "close_position",
    "close_all_positions",
    "exercise_options_position",
    "do_not_exercise_options_position",
}


def is_trade_tool(tool_name: str) -> bool:
    return tool_name in TRADE_TOOL_NAMES


# ── Risk gate ────────────────────────────────────────────────────────────

def run_risk_checks(
    tool_name: str,
    arguments: dict,
    context: dict,
    settings,
) -> tuple[bool, str]:
    """
    Hard backstop applied to every trade-placing tool call before it's
    allowed to execute. Returns (allowed, reason).

    `context` is the memory snapshot from agent/memory.py — it must contain
    at minimum: account (dict with 'equity'/'last_equity'), open_positions
    (dict), trades_today (list of dicts with 'pnl').
    """
    # 1. Kill switch — the same check main.py does per-cycle, repeated here
    #    so a tool call can never slip through between cycle-level checks.
    can_trade, reason = check_kill_switch()
    if not can_trade:
        return False, reason

    account = context.get("account") or {}
    equity = float(account.get("equity", 0) or 0)
    last_equity = float(account.get("last_equity", equity) or equity)

    # 2. Drawdown guard
    if equity > 0 and last_equity > 0:
        ok, reason = check_drawdown_limit(
            initial_balance=last_equity,
            current_balance=equity,
            max_drawdown=settings.DAILY_LOSS_LIMIT,
        )
        if not ok:
            return False, reason

    # 3. Daily trade count limit
    trades_today = context.get("trades_today", [])
    if len(trades_today) >= settings.MAX_TRADES_PER_DAY:
        return False, (
            f"Max trades per day reached: {len(trades_today)}"
            f"/{settings.MAX_TRADES_PER_DAY}"
        )

    # 4. Daily loss limit (realized $ loss today vs. equity-based limit)
    daily_loss_dollar_limit = equity * settings.DAILY_LOSS_LIMIT
    realized_loss_today = sum(
        abs(t.get("pnl", 0)) for t in trades_today if t.get("pnl", 0) < 0
    )
    if realized_loss_today >= daily_loss_dollar_limit > 0:
        return False, (
            f"Daily loss limit reached: ${realized_loss_today:.2f} "
            f">= ${daily_loss_dollar_limit:.2f}"
        )

    # 5. Max concurrent positions (only relevant for tools that open new risk)
    if tool_name in {"place_option_order", "place_stock_order", "place_crypto_order"}:
        open_positions = context.get("open_positions", {}) or {}
        if len(open_positions) >= settings.MAX_POSITIONS:
            return False, (
                f"Max concurrent positions reached: {len(open_positions)}"
                f"/{settings.MAX_POSITIONS}"
            )

        # 6. Universe check — only trade underlyings we're configured for.
        symbol = str(arguments.get("symbol") or arguments.get("underlying_symbol") or "").upper()
        underlying = symbol.split("_")[0].rstrip("0123456789CP") if symbol else ""
        if settings.OPTIONS_UNIVERSE and underlying and underlying not in settings.OPTIONS_UNIVERSE:
            return False, (
                f"Symbol '{symbol}' underlying not in configured universe "
                f"{settings.OPTIONS_UNIVERSE}"
            )

        # 7. Position sizing sanity check, if the model supplied a notional.
        notional = arguments.get("notional") or arguments.get("qty_notional")
        if notional and equity > 0:
            pct = float(notional) / equity
            if pct > settings.MAX_POSITION_PCT:
                return False, (
                    f"Requested position {pct:.1%} of equity exceeds "
                    f"MAX_POSITION_PCT {settings.MAX_POSITION_PCT:.1%}"
                )

        # 8. Single-asset exposure check (best-effort — uses cost-basis proxy).
        if underlying:
            ok, reason = check_single_asset_exposure(
                positions=open_positions,
                new_coin=underlying,
                new_size=float(notional or 0),
                total_portfolio_value=equity or 1.0,
                max_single_asset_pct=settings.MAX_POSITION_PCT,
            )
            if not ok:
                return False, reason

    # 9. Signal timing — a decision that sat around too long shouldn't be
    #    executed against stale prices. `context["generated_at"]` is set by
    #    agent/memory.py at snapshot time.
    generated_at_raw = context.get("generated_at")
    if generated_at_raw:
        try:
            generated_at = datetime.fromisoformat(generated_at_raw)
            ok, reason = check_execution_timing(generated_at, max_age_seconds=settings.BOT_INTERVAL_SECONDS * 2)
            if not ok:
                return False, reason
        except (ValueError, TypeError):
            pass  # don't block a trade over a malformed timestamp

    return True, "All risk checks passed"


# ── MCP dispatch ─────────────────────────────────────────────────────────

async def call_mcp_tool(session, tool_name: str, arguments: dict) -> dict:
    """
    Invoke a tool on the connected Alpaca MCP session and return a plain
    dict result. MCP text-content blocks are JSON-decoded when possible.
    """
    logger.info(f"Calling MCP tool: {tool_name}({arguments})")
    result = await session.call_tool(tool_name, arguments)

    texts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(text)

    raw = "\n".join(texts).strip()

    parsed: dict
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"raw_text": raw}

    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "is_error": bool(getattr(result, "isError", False)),
        "result": parsed,
    }


async def execute_decision(session, decision: dict, context: dict, settings) -> dict:
    """
    Given a parsed model decision (see agent/prompts.py response schema),
    either execute it (with risk-gating for trade tools) or return a
    no-op result.

    Always returns a dict describing what happened — never raises for
    "expected" outcomes like a rejected trade, so the caller can log and
    move on. Genuine exceptions (MCP transport errors, etc.) propagate.
    """
    action = decision.get("action")

    if action == "no_action":
        return {"action": "no_action", "reasoning": decision.get("reasoning", "")}

    if action != "call_tool":
        return {
            "action": "invalid",
            "reasoning": f"Unrecognized action from model: {action!r}",
        }

    tool_name = decision.get("tool_name", "")
    arguments = decision.get("arguments") or {}

    if is_trade_tool(tool_name):
        allowed, reason = run_risk_checks(tool_name, arguments, context, settings)
        if not allowed:
            logger.warning(f"Risk gate BLOCKED {tool_name}: {reason}")
            return {
                "action": "blocked",
                "tool_name": tool_name,
                "arguments": arguments,
                "reason": reason,
            }

    tool_result = await call_mcp_tool(session, tool_name, arguments)
    return {
        "action": "executed",
        "reasoning": decision.get("reasoning", ""),
        **tool_result,
    }