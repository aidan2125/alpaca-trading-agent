"""
agent/prompts.py — Prompt construction for the options-trading reasoning loop.

GLM-5.2 (via Featherless) is called as a plain chat-completions endpoint —
no native Anthropic-style `tools` param is sent (see agent/loop.py). So the
"tool use" contract lives entirely in these prompts: we describe the
available Alpaca MCP tools in plain text, and require the model to answer
in a strict JSON envelope that agent/loop.py parses and executes.

Keep this single-tool-call-per-cycle. main.py already re-invokes run_cycle
every BOT_INTERVAL_SECONDS, so the model gets a fresh look at the world on
the next tick rather than needing a multi-step plan in one shot.
"""

import json


RESPONSE_SCHEMA_INSTRUCTIONS = """
You must respond with ONLY a single JSON object — no prose before or after,
no markdown code fences. It must match exactly one of these two shapes:

1) To call a tool:
{
  "reasoning": "<1-3 sentences on why>",
  "action": "call_tool",
  "tool_name": "<one of the tool names listed above>",
  "arguments": { ... arguments matching that tool's schema ... }
}

2) To do nothing this cycle:
{
  "reasoning": "<1-3 sentences on why no action is warranted right now>",
  "action": "no_action"
}

Never invent a tool name that wasn't listed. Never place more than one
order per cycle. If you are unsure a proposed trade is safe, choose
"no_action" and explain why in "reasoning" — being idle is always an
acceptable, safe outcome.
""".strip()


def format_tools_for_prompt(tools: list[dict]) -> str:
    """
    Render the MCP tool catalog (Anthropic-schema dicts, as produced by
    main.py's _mcp_tools_to_anthropic_schema) into a plain-text list for
    a model that isn't receiving them as structured `tools`.
    """
    lines = []
    for tool in tools:
        name = tool.get("name", "unknown_tool")
        desc = (tool.get("description") or "").strip().split("\n")[0]
        schema = tool.get("input_schema", {})
        props = schema.get("properties", {})
        required = set(schema.get("required", []))

        if props:
            arg_bits = []
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                mark = "*" if pname in required else ""
                arg_bits.append(f"{pname}{mark}: {ptype}")
            arg_str = ", ".join(arg_bits)
        else:
            arg_str = "(no arguments)"

        lines.append(f"- {name}({arg_str}) — {desc}")

    lines.append("\n(* = required argument)")
    return "\n".join(lines)


def build_system_prompt(settings, tools: list[dict]) -> str:
    """Build the system message for one reasoning cycle."""
    tool_catalog = format_tools_for_prompt(tools)

    return f"""You are an autonomous options-trading agent operating an Alpaca
{"PAPER" if settings.ALPACA_PAPER else "LIVE"} TRADING account. You trade
options only — do not attempt to trade raw stock or crypto positions unless
explicitly instructed to hedge or close an existing non-option position.

You never assume an order executed unless a tool result explicitly confirms
it. You are conservative by default: skipping a cycle is always safe, an
unnecessary trade is not.

## Your trading mandate
- Underlying universe: {", ".join(settings.OPTIONS_UNIVERSE)}
- Days-to-expiration window: {settings.OPTIONS_MIN_DTE}-{settings.OPTIONS_MAX_DTE} days
- Max concurrent positions: {settings.MAX_POSITIONS}
- Max trades per day: {settings.MAX_TRADES_PER_DAY}
- Max risk per trade: {settings.MAX_RISK_PER_TRADE:.1%} of account value
- Max position size: {settings.MAX_POSITION_PCT:.1%} of account value
- Daily loss limit: {settings.DAILY_LOSS_LIMIT:.1%} of account value
- Trade cooldown: {settings.TRADE_COOLDOWN_HOURS}h between trades on the same underlying
- Minimum signal quality to act on: {settings.MIN_SIGNAL_QUALITY}/100

These limits are enforced in code as a hard backstop (agent/tools.py), not
just guidance — a proposed trade that violates them will be rejected before
it reaches Alpaca. Don't rely on the backstop, though: reason about risk
yourself first.

## Available tools (Alpaca MCP server)
{tool_catalog}

## Response format
{RESPONSE_SCHEMA_INSTRUCTIONS}
"""


def build_user_prompt(context: dict) -> str:
    """
    Build the user message for one reasoning cycle from a memory snapshot
    (see agent/memory.py: AgentMemory.snapshot()).
    """
    return (
        "Here is the current account and trading context:\n\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        "Decide the single best action for this cycle. If you need "
        "market data (quotes, option chains, Greeks) before deciding "
        "whether to trade, call a read-only data tool this cycle — you'll "
        "get another cycle shortly to act on what you learn. Respond with "
        "the JSON object described in your instructions, nothing else."
    )


def build_status_prompt_no_tools() -> list[dict]:
    """
    The original Step-1 connectivity check messages (kept for reference /
    smoke-testing the Featherless connection in isolation).
    """
    return [
        {
            "role": "system",
            "content": (
                "You are an AI trading assistant operating in PAPER TRADING mode. "
                "You must never assume that an order has been executed unless a "
                "trading tool explicitly confirms it. "
                "For this test, do not attempt to place any trades."
            ),
        },
        {
            "role": "user",
            "content": (
                "Give me a short status message confirming that you are connected "
                "and ready to receive Alpaca account and market information."
            ),
        },
    ]