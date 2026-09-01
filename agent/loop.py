"""
agent/loop.py — Agent reasoning loop.

STUB: this currently only proves the wiring (MCP session + tool schema +
settings all reach this function correctly). The next step replaces the
body of run_cycle() with the real Claude tool-use loop:
  1. Call Anthropic Messages API with `tools=tools`, a system prompt from
     agent/prompts.py, and portfolio/market context
  2. For each tool_use block Claude returns, execute it via
     `await session.call_tool(name, arguments)`
  3. Feed tool_result(s) back to Claude, repeat until it stops calling tools
  4. Apply risk checks (position_sizer, exposure_limiter, drawdown_guard)
     before any order-placing tool call is actually executed
  5. Log the resulting decision/trade via database/trade_logger.py
"""

import logging

logger = logging.getLogger(__name__)


async def run_cycle(session, tools: list[dict], settings) -> None:
    """
    Run one agent decision cycle.

    Args:
        session: an open mcp.ClientSession connected to Alpaca's MCP server
        tools: Anthropic-schema tool definitions discovered from that session
        settings: config.settings module
    """
    logger.info(f"[stub] run_cycle called with {len(tools)} tools available — no reasoning logic yet")
    # TODO: replace with real Claude tool-use loop (see module docstring)