"""
main.py — Entrypoint.

Responsibilities:
  1. Load settings/.env
  2. Spawn Alpaca's official MCP server (mcp/alpaca_mcp_config.json) as a
     local stdio subprocess and open an MCP ClientSession against it
  3. Discover its tools and convert them to Anthropic tool-use schema
  4. Check the kill switch, then hand control to agent.loop for one
     reasoning/trading cycle
  5. Sleep BOT_INTERVAL_SECONDS and repeat
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import settings
from monitoring.logger import setup_logging
from monitoring.heartbeat import log_heartbeat, send_telegram
from risk.kill_switch import check_kill_switch
from alerts.discord_alerts import send_discord_message
from alerts.telegram_alerts import send_telegram_message
from agent.loop import run_cycle

ROOT = Path(__file__).parent
MCP_CONFIG_PATH = ROOT / "mcp" / "alpaca_mcp_config.json"

logger = logging.getLogger(__name__)


def _load_mcp_server_params() -> StdioServerParameters:
    """
    Read mcp/alpaca_mcp_config.json and substitute ${VAR} placeholders with
    real values from the environment (via config/settings.py), so secrets
    never live in the checked-in json file.
    """
    raw = MCP_CONFIG_PATH.read_text()

    def _sub(match: "re.Match") -> str:
        key = match.group(1)
        return os.environ.get(key, "")

    resolved = re.sub(r"\$\{([A-Z_]+)\}", _sub, raw)
    cfg = json.loads(resolved)
    alpaca_cfg = cfg["mcpServers"]["alpaca"]

    env = dict(alpaca_cfg.get("env", {}))
    # ALPACA_PAPER_TRADE isn't a real env var elsewhere in the project —
    # derive it from settings.ALPACA_PAPER so .env only needs ALPACA_MODE.
    env["ALPACA_PAPER_TRADE"] = "True" if settings.ALPACA_PAPER else "False"

    return StdioServerParameters(
        command=alpaca_cfg["command"],
        args=alpaca_cfg["args"],
        env=env,
    )


def _mcp_tools_to_anthropic_schema(mcp_tools) -> list[dict]:
    """Convert MCP tool definitions into the shape the Anthropic Messages API expects."""
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        }
        for tool in mcp_tools
    ]


def _start_heartbeat_thread() -> None:
    def _loop():
        count = 0
        while True:
            count += 1
            log_heartbeat(count)
            if count % 6 == 0:  # every 30 min at the default 5-min interval
                send_telegram(f"\U0001F493 Agent heartbeat — cycle {count}")
            time.sleep(300)

    threading.Thread(target=_loop, daemon=True).start()


def _notify_halt(reason: str) -> None:
    logger.warning(f"Kill switch active — skipping cycle: {reason}")
    send_telegram_message(f"\u26D4 Trading halted: {reason}")
    send_discord_message(f"\u26D4 Trading halted: {reason}")


async def run() -> None:
    setup_logging(log_dir=settings.LOG_DIR, level=settings.LOG_LEVEL)
    logger.info(f"Starting agent — mode={settings.TRADING_MODE}, paper={settings.ALPACA_PAPER}")

    if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
        logger.error("ALPACA_API_KEY / ALPACA_SECRET_KEY not set — check .env")
        return

    _start_heartbeat_thread()

    server_params = _load_mcp_server_params()

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            anthropic_tools = _mcp_tools_to_anthropic_schema(tools_result.tools)
            logger.info(f"Connected to Alpaca MCP server — {len(anthropic_tools)} tools available")

            while True:
                halted, reason = check_kill_switch()
                if halted:
                    _notify_halt(reason)
                else:
                    try:
                        await run_cycle(session=session, tools=anthropic_tools, settings=settings)
                    except Exception as e:
                        logger.exception(f"Agent cycle failed: {e}")
                        send_telegram_message(f"\u26A0\uFE0F Agent cycle error: {e}")

                await asyncio.sleep(settings.BOT_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run())