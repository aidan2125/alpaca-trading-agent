"""
agent/loop.py — Featherless / GLM-5.2 reasoning loop.

Step 1:
    Prove that the trading agent can send a prompt to GLM-5.2
    through Featherless and receive a response.

No trading tools or orders are executed yet.
"""

import logging
import requests

logger = logging.getLogger(__name__)


def _call_featherless(messages: list[dict], settings) -> str:
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

    return data["choices"][0]["message"]["content"]


async def run_cycle(session, tools: list[dict], settings) -> None:
    """
    Run one agent decision cycle.

    For now this only tests the Featherless connection.
    Alpaca MCP tools will be connected in the next step.
    """

    logger.info("Starting agent reasoning cycle")

    messages = [
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

    result = _call_featherless(messages, settings)

    logger.info("GLM-5.2 response received successfully")

    print("\n" + "=" * 70)
    print("GLM-5.2 RESPONSE")
    print("=" * 70)
    print(result)
    print("=" * 70 + "\n")