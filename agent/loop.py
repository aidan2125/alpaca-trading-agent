"""
agent/loop.py — Featherless / GLM-5.2 reasoning loop with tool calling.

Step 4 (from "cycles are observable" -> "trading tools are gated"):
    Every MCP tool call now passes through risk.agent_safety_gate.enforce_gate()
    before dispatch. If the tool is one of TRADING_TOOLS (places, modifies,
    cancels, or closes an order/position) and EXECUTION_ENABLED is False,
    the call is blocked — the model gets a clear "blocked, dry run" tool
    result instead of the order actually reaching Alpaca, and the cycle's
    logged decision is status="BLOCKED" rather than "INFO"/"TRADED", so it
    shows up correctly in the dashboard's Trades panel (which already
    expects a BLOCKED status, per dashboard/data.py's TRADING_TOOLS filter).
    Non-trading MCP tools (get_account_info, get_orders, etc.) and local
    tools (run_options_backtest) are unaffected — the gate only ever
    intercepts TRADING_TOOLS names.

Step 3 (previous):
  1. Every completed cycle writes exactly one row to TradeLogger via
     log_decision(), even on a no-op cycle.
  2. run_options_backtest calls are deduped within a single cycle by
     identical arguments.

This module still:
  - Builds the OpenAI-shaped `tools` payload from MCP tools (passed in,
    already OpenAI-shaped by main.py) plus local in-process tools
    (currently just run_options_backtest).
  - Runs a real multi-round dispatch loop: send messages -> check for
    tool_calls -> execute each -> append role:"tool" results -> send
    again -> repeat until the model responds with plain content.
  - Keeps full message history across rounds (GLM needs prior thinking
    retained between tool rounds).

TODO(aidan): confirm once you're back in the repo —
  - FEATHERLESS_BASE_URL / FEATHERLESS_MODEL settings names, unchanged.
  - settings.EXECUTION_ENABLED: risk/agent_safety_gate.py reads this via
    getattr(settings, "EXECUTION_ENABLED", False), so it's safe even if
    that field doesn't exist yet in config/settings.py — but you'll want
    to actually add it there (env-var-backed, like everything else) once
    you're ready to allow live order placement. Until then every trading
    tool call is blocked by default, which is the safe starting state.
  - MAX_TOOL_ROUNDS is a safety cap, tune to taste.
"""

import json
import logging

import requests

from database.trade_logger import TradeLogger
from risk.agent_safety_gate import enforce_gate, is_trading_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8

_trade_logger = TradeLogger()


# ────────────────────────────────────────────────
# Local (non-MCP) tools
# ────────────────────────────────────────────────
def _run_options_backtest_tool(
    csv_path: str | None = None,
    underlying: str | None = None,
    contract: str | None = None,
    timeframe: str = "1h",
    lookback_days: int = 30,
    target_dte: int = 30,
    dte_window: int = 7,
    expiry_exit_dte: int = 1,
    initial_balance: float = 10000,
) -> dict:
    """
    Runs OptionsBacktestPro in-process and returns the summary dict
    (trades, win_rate, profit_factor, total_pnl, final_balance, roi,
    max_drawdown) so the model can see it as a tool result mid-reasoning,
    before deciding whether to place a trade.

    Mirrors options_backtest.py's CLI precedence: csv_path takes priority
    over underlying (full underlying -> contract -> signal pipeline) which
    takes priority over contract (raw premium fetch, needs pre-built
    signal data — will produce zero trades on its own without csv_path).
    """
    from backtest.backtest_agent import OptionsBacktestPro, load_risk_config

    import polars as pl

    risk_config = load_risk_config()
    symbol_label = contract or underlying or "OPTION"

    if csv_path:
        df = pl.read_csv(csv_path, try_parse_dates=True)
    elif underlying:
        from strategies.options_signals import generate_options_backtest_frame
        from config import settings as _settings

        df, picked_contract = generate_options_backtest_frame(
            underlying,
            _settings.ALPACA_API_KEY,
            _settings.ALPACA_SECRET_KEY,
            timeframe=timeframe,
            lookback_days=lookback_days,
            target_dte=target_dte,
            dte_window=dte_window,
        )
        if df is None:
            return {"error": f"No trade set up for {underlying} (no signal or no matching contract)."}
        symbol_label = picked_contract
    elif contract:
        from data.options_data import fetch_option_ohlcv

        df = fetch_option_ohlcv(contract, timeframe, lookback_days)
        if not df.is_empty() and "signal" not in df.columns:
            return {
                "error": (
                    "Fetched premium data has no 'signal' column — pass "
                    "'underlying' to run the full signal pipeline, or "
                    "'csv_path' with signal/stop_loss/take_profit already "
                    "computed."
                )
            }
    else:
        return {"error": "Need one of csv_path, underlying, or contract."}

    if df.is_empty():
        return {"error": "No data to backtest."}

    backtester = OptionsBacktestPro(
        initial_balance=initial_balance,
        risk_config=risk_config,
        expiry_exit_dte=expiry_exit_dte,
    )
    summary = backtester.run(df, symbol=symbol_label, strategy_name="Options Strategy")
    summary["contract"] = symbol_label
    return summary


LOCAL_TOOLS = {
    "run_options_backtest": _run_options_backtest_tool,
}

# Tool names allowed to be served from the in-cycle cache on a repeat call
# with identical arguments. Only pure/deterministic-for-this-cycle tools
# belong here — never MCP tools, since those reflect live account/market
# state that can legitimately change between calls.
DEDUP_ELIGIBLE_TOOLS = {"run_options_backtest"}

LOCAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_options_backtest",
            "description": (
                "Backtest an options strategy (long calls/puts, premium-based "
                "P&L, contract-count sizing) and return a summary with trade "
                "count, win rate, profit factor, total P&L, final balance, "
                "ROI, and max drawdown. Use this BEFORE deciding whether to "
                "place an options trade, to see how the underlying strategy "
                "has performed historically. Provide either 'underlying' "
                "(runs the full underlying -> contract selection -> signal "
                "pipeline and picks a near-ATM contract automatically), "
                "'contract' (a specific OCC option symbol — needs csv_path "
                "too, or it will produce zero trades), or 'csv_path' "
                "(pre-built premium+signal data). Calling this again with "
                "the exact same arguments in the same cycle will not "
                "produce new information — try a different symbol/contract "
                "instead, or conclude the cycle if nothing looks tradeable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "underlying": {
                        "type": "string",
                        "description": "Underlying stock symbol, e.g. AAPL. Preferred: runs the full pipeline.",
                    },
                    "contract": {
                        "type": "string",
                        "description": "OCC option symbol, e.g. AAPL250117C00150000.",
                    },
                    "csv_path": {
                        "type": "string",
                        "description": "Path to a CSV with close, signal, dte (and optionally stop_loss/take_profit/signal_quality) columns.",
                    },
                    "timeframe": {"type": "string", "description": "Bar timeframe, e.g. '1h'. Default '1h'."},
                    "lookback_days": {"type": "integer", "description": "Days of history to pull. Default 30."},
                    "target_dte": {"type": "integer", "description": "Target days-to-expiration for contract auto-selection. Default 30."},
                    "dte_window": {"type": "integer", "description": "+/- days around target_dte to search. Default 7."},
                    "expiry_exit_dte": {"type": "integer", "description": "Force-close any open position at or below this DTE. Default 1."},
                    "initial_balance": {"type": "number", "description": "Starting paper balance for the backtest. Default 10000."},
                },
                "required": [],
            },
        },
    }
]


# ────────────────────────────────────────────────
# Featherless call
# ────────────────────────────────────────────────
def _call_featherless(messages: list[dict], settings, tools: list[dict] | None = None) -> dict:
    """
    Send a chat completion request to Featherless. Returns the raw
    `message` dict from choices[0] (role, content, and — if the model
    chose to call a tool instead of/alongside responding — tool_calls),
    rather than just the text content, so the caller can branch on
    whether a tool call happened.
    """

    if not settings.FEATHERLESS_API_KEY:
        raise RuntimeError("FEATHERLESS_API_KEY is not set")

    url = f"{settings.FEATHERLESS_BASE_URL}/chat/completions"

    headers = {
        "Authorization": f"Bearer {settings.FEATHERLESS_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.FEATHERLESS_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1500,
    }
    if tools:
        payload["tools"] = tools

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=90,
    )

    if not response.ok:
        logger.error(f"Featherless {response.status_code} response body: {response.text}")

    response.raise_for_status()

    data = response.json()

    return data["choices"][0]["message"]


# ────────────────────────────────────────────────
# Tool dispatch
# ────────────────────────────────────────────────
async def _execute_tool_call(tool_call: dict, session, call_cache: dict, cycle_flags: dict) -> str:
    """
    Executes a single tool call — routing to a local Python function if
    the name is in LOCAL_TOOLS, otherwise assuming it's an MCP tool and
    calling it through the Alpaca MCP session (after the safety gate).

    call_cache is a plain dict scoped to one run_cycle() call, keyed by
    (tool_name, sorted-args-json). Only tools in DEDUP_ELIGIBLE_TOOLS are
    ever served from it.

    cycle_flags is a plain dict scoped to one run_cycle() call, mutated
    here to record whether any trading-tool call was BLOCKED or actually
    reached Alpaca this cycle, so run_cycle can log the right status.
    """
    name = tool_call["function"]["name"]
    raw_args = tool_call["function"].get("arguments") or "{}"

    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Malformed arguments for {name}: {e}"})

    # Safety gate — only ever intercepts TRADING_TOOLS names.
    if is_trading_tool(name):
        block = enforce_gate(name, args)
        if block is not None:
            cycle_flags["blocked"] = True
            cycle_flags.setdefault("blocked_tools", []).append(name)
            return json.dumps(block)
        cycle_flags["traded"] = True
        cycle_flags.setdefault("traded_tools", []).append(name)

    cache_key = None
    if name in DEDUP_ELIGIBLE_TOOLS:
        cache_key = (name, json.dumps(args, sort_keys=True))
        if cache_key in call_cache:
            logger.info(f"Serving {name}{args} from in-cycle cache (identical repeat call)")
            return call_cache[cache_key]

    if name in LOCAL_TOOLS:
        try:
            result = LOCAL_TOOLS[name](**args)
        except Exception as e:
            logger.exception(f"Local tool {name} raised")
            result = {"error": f"{name} failed: {e}"}
        result_str = json.dumps(result, default=str)
        if cache_key is not None:
            call_cache[cache_key] = result_str
        return result_str

    # Otherwise, dispatch through the MCP session (Alpaca tools) — never
    # cached, live account/market state can legitimately change call to call.
    try:
        mcp_result = await session.call_tool(name, arguments=args)
        text_parts = [
            block.text for block in getattr(mcp_result, "content", []) if hasattr(block, "text")
        ]
        return "\n".join(text_parts) if text_parts else json.dumps({"result": "ok", "raw": str(mcp_result)})
    except Exception as e:
        logger.exception(f"MCP tool {name} failed")
        return json.dumps({"error": f"{name} failed: {e}"})


# ────────────────────────────────────────────────
# Main cycle
# ────────────────────────────────────────────────
async def run_cycle(session, tools: list[dict], settings) -> None:
    """
    Run one agent decision cycle with real tool calling, and log exactly
    one decision row to TradeLogger before returning — status reflects
    whether a trading tool was blocked, actually reached Alpaca, or the
    cycle simply completed with no action / hit an error.
    """

    logger.info("Starting agent reasoning cycle")

    all_tools = list(tools) + LOCAL_TOOL_SCHEMAS
    call_cache: dict = {}
    cycle_flags: dict = {}  # {"blocked": bool, "traded": bool, "blocked_tools": [...], "traded_tools": [...]}

    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI trading assistant operating in PAPER TRADING mode. "
                "You must never assume that an order has been executed unless a "
                "trading tool explicitly confirms it. "
                "Before placing any options trade, use run_options_backtest to "
                "check how the strategy has performed historically (win rate, "
                "profit factor, ROI, max drawdown) and factor that into your "
                "decision. Do not call run_options_backtest again with the same "
                "arguments — if a symbol shows no signal, either try a different "
                "symbol or conclude the cycle with no action. Explain your "
                "reasoning before acting."
            ),
        },
        {
            "role": "user",
            "content": (
                "Review current account/market conditions and, if appropriate, "
                "evaluate a trade — backtest it first, then decide."
            ),
        },
    ]

    final_text = None
    hit_round_limit = False

    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        try:
            message = _call_featherless(messages, settings, tools=all_tools)
        except Exception as e:
            logger.exception("Featherless call failed")
            _log_cycle_decision(
                status="ERROR",
                decision="FEATHERLESS_CALL_FAILED",
                reasoning=str(e),
            )
            raise

        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            final_text = message.get("content")
            break

        logger.info(f"Round {round_num}: model requested {len(tool_calls)} tool call(s)")

        for tool_call in tool_calls:
            tool_result = await _execute_tool_call(tool_call, session, call_cache, cycle_flags)
            messages.append(
                {
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "name": tool_call["function"]["name"],
                    "content": tool_result,
                }
            )
    else:
        hit_round_limit = True
        logger.warning(f"Hit MAX_TOOL_ROUNDS ({MAX_TOOL_ROUNDS}) without a final response")
        final_text = messages[-1].get("content") or "(no final response — tool round limit hit)"

    logger.info("Agent reasoning cycle complete")

    print("\n" + "=" * 70)
    print("GLM-5.2 FINAL RESPONSE")
    print("=" * 70)
    print(final_text)
    print("=" * 70 + "\n")

    if hit_round_limit:
        status, decision = "ERROR", "ROUND_LIMIT_HIT"
    elif cycle_flags.get("traded"):
        status, decision = "TRADED", ", ".join(cycle_flags.get("traded_tools", []))
    elif cycle_flags.get("blocked"):
        status, decision = "BLOCKED", ", ".join(cycle_flags.get("blocked_tools", []))
    else:
        status, decision = "INFO", "CYCLE_COMPLETE"

    _log_cycle_decision(status=status, decision=decision, reasoning=final_text)


def _log_cycle_decision(status: str, decision: str, reasoning: str | None, symbol: str | None = None) -> None:
    """
    Best-effort wrapper around TradeLogger.log_decision — a logging
    failure should never take down the trading cycle itself.
    """
    try:
        _trade_logger.log_decision(
            decision=decision,
            status=status,
            symbol=symbol,
            reasoning=reasoning,
        )
    except Exception:
        logger.exception("Failed to log decision to TradeLogger")