"""
agent/loop.py — Featherless / GLM-5.2 reasoning loop.

GLM-5.2 -> Featherless -> MCP -> Python safety gate -> Alpaca.

Trading tools are intercepted by the Python safety gate BEFORE
session.call_tool() can execute them.
"""

import json
import logging
import requests

from risk.agent_safety_gate import (
    TRADING_TOOLS,
    check_agent_order_safety,
)

logger = logging.getLogger(__name__)


# Read-only tools allowed without the trading safety gate.
ALLOWED_READ_ONLY_TOOLS = {
    "get_account_info",
    "get_all_positions",
}


def _call_featherless(
    messages: list[dict],
    tools: list[dict],
    settings,
) -> dict:
    """Send a chat completion request to Featherless."""

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
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": 500,
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    return data["choices"][0]["message"]


def _mcp_result_to_text(result) -> str:
    """Convert an MCP CallToolResult into text."""

    if hasattr(result, "model_dump"):
        try:
            dumped = result.model_dump()
            return json.dumps(dumped, default=str)
        except Exception:
            pass

    if hasattr(result, "content"):
        parts = []

        for item in result.content:
            if hasattr(item, "text"):
                parts.append(item.text)

            elif hasattr(item, "model_dump"):
                try:
                    parts.append(
                        json.dumps(item.model_dump(), default=str)
                    )
                except Exception:
                    parts.append(str(item))

            else:
                parts.append(str(item))

        return "\n".join(parts)

    return str(result)


async def _get_current_position_count(session) -> int:
    """
    Get the current number of Alpaca positions.

    Reads the structured MCP response from get_all_positions.
    Fails closed if the response cannot be interpreted.
    """

    try:
        result = await session.call_tool(
            "get_all_positions",
            {},
        )

        # Prefer structured MCP content when available.
        structured = getattr(result, "structuredContent", None)

        if isinstance(structured, dict):
            data = structured.get("data", {})
            positions = data.get("result")

            if isinstance(positions, list):
                logger.info(
                    "Current Alpaca positions: %d",
                    len(positions),
                )
                return len(positions)

        # Fallback: inspect the serialized MCP response.
        text = _mcp_result_to_text(result)

        try:
            parsed = json.loads(text)

            if isinstance(parsed, dict):
                structured = parsed.get(
                    "structuredContent",
                    {},
                )

                data = structured.get("data", {})
                positions = data.get("result")

                if isinstance(positions, list):
                    logger.info(
                        "Current Alpaca positions: %d",
                        len(positions),
                    )
                    return len(positions)

        except json.JSONDecodeError:
            pass

        logger.warning(
            "Could not parse position response for safety check"
        )

    except Exception as exc:
        logger.exception(
            "Failed to retrieve positions for safety check: %s",
            exc,
        )

    # IMPORTANT:
    # If we cannot determine current positions,
    # fail closed and block the trade.
    return -1


async def _execute_tool(
    session,
    tool_name: str,
    arguments: dict,
    settings,
) -> str:
    """
    Execute an MCP tool safely.

    Trading tools MUST pass through the Python safety gate
    before session.call_tool() is allowed.
    """

    # ---------------------------------------------------------
    # READ-ONLY TOOLS
    # ---------------------------------------------------------

    if tool_name in ALLOWED_READ_ONLY_TOOLS:

        logger.info(
            "Executing approved read-only MCP tool: %s",
            tool_name,
        )

        result = await session.call_tool(
            tool_name,
            arguments,
        )

        return _mcp_result_to_text(result)

    # ---------------------------------------------------------
    # TRADING TOOLS
    # ---------------------------------------------------------

    if tool_name in TRADING_TOOLS:

        current_positions = await _get_current_position_count(
            session
        )

        # Fail closed if we cannot determine positions.
        if current_positions < 0:
            return json.dumps(
                {
                    "success": False,
                    "blocked": True,
                    "reason": (
                        "Safety check failed: unable to determine "
                        "current Alpaca positions."
                    ),
                    "tool": tool_name,
                }
            )

        allowed, reason = check_agent_order_safety(
            tool_name=tool_name,
            arguments=arguments,
            current_positions=current_positions,
            settings=settings,
        )

        # -----------------------------------------------------
        # BLOCKED
        # -----------------------------------------------------

        if not allowed:

            logger.warning(
                "SAFETY GATE BLOCKED %s: %s",
                tool_name,
                reason,
            )

            return json.dumps(
                {
                    "success": False,
                    "blocked": True,
                    "reason": reason,
                    "tool": tool_name,
                }
            )

        # -----------------------------------------------------
        # APPROVED
        # -----------------------------------------------------

        logger.warning(
            "SAFETY GATE APPROVED %s: %s",
            tool_name,
            reason,
        )

        result = await session.call_tool(
            tool_name,
            arguments,
        )

        return _mcp_result_to_text(result)

    # ---------------------------------------------------------
    # UNKNOWN TOOL
    # ---------------------------------------------------------

    raise PermissionError(
        f"Tool '{tool_name}' is not explicitly approved."
    )


async def run_cycle(
    session,
    tools: list[dict],
    settings,
) -> None:
    """
    Run one agent decision cycle.

    GLM can request Alpaca MCP tools.

    Read-only tools execute normally.

    Trading tools are intercepted by the Python safety gate
    BEFORE they can reach Alpaca.
    """

    logger.info("Starting agent reasoning cycle")

    messages = [
        {
            "role": "system",
            "content": (
                "You are an AI trading assistant operating in PAPER "
                "TRADING mode. You are connected to Alpaca through MCP "
                "tools. "
                "Never claim an action happened unless the MCP tool "
                "explicitly confirms it. "
                "Follow all safety restrictions. "
                "Do not bypass or attempt to circumvent Python safety "
                "controls."
            ),
        },
        {
            "role": "user",
            "content": (
                "Use the get_account_info tool to retrieve the current "
                "Alpaca paper trading account information. "
                "Then give me a short summary of the account status."
            ),
        },
    ]

    # ---------------------------------------------------------
    # FIRST GLM CALL
    # ---------------------------------------------------------

    assistant_message = _call_featherless(
        messages,
        tools,
        settings,
    )

    messages.append(assistant_message)

    tool_calls = assistant_message.get(
        "tool_calls",
        [],
    )

    # No tool requested.
    if not tool_calls:

        result = assistant_message.get(
            "content",
            "",
        )

        logger.info(
            "GLM-5.2 responded without requesting an MCP tool"
        )

        print("\n" + "=" * 70)
        print("GLM-5.2 RESPONSE")
        print("=" * 70)
        print(result)
        print("=" * 70 + "\n")

        return

    # ---------------------------------------------------------
    # EXECUTE REQUESTED TOOLS
    # ---------------------------------------------------------

    for tool_call in tool_calls:

        tool_call_id = tool_call.get("id")

        function = tool_call.get(
            "function",
            {},
        )

        tool_name = function.get("name")

        raw_arguments = function.get(
            "arguments",
            "{}",
        )

        try:
            arguments = json.loads(raw_arguments)

        except json.JSONDecodeError as exc:

            raise RuntimeError(
                f"Invalid JSON arguments from GLM for tool "
                f"'{tool_name}': {raw_arguments}"
            ) from exc

        logger.info(
            "GLM-5.2 requested MCP tool: %s(%s)",
            tool_name,
            arguments,
        )

        try:

            tool_result = await _execute_tool(
                session=session,
                tool_name=tool_name,
                arguments=arguments,
                settings=settings,
            )

        except Exception as exc:

            logger.exception(
                "MCP tool execution failed: %s",
                tool_name,
            )

            tool_result = json.dumps(
                {
                    "error": str(exc),
                    "tool": tool_name,
                }
            )

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result,
            }
        )

    # ---------------------------------------------------------
    # SECOND GLM CALL
    # ---------------------------------------------------------

    final_message = _call_featherless(
        messages,
        tools,
        settings,
    )

    result = final_message.get(
        "content",
        "",
    )

    logger.info(
        "GLM-5.2 processed MCP tool results successfully"
    )

    print("\n" + "=" * 70)
    print("GLM-5.2 + ALPACA MCP RESPONSE")
    print("=" * 70)
    print(result)
    print("=" * 70 + "\n")