import asyncio
import json

from config import settings
from main import _load_mcp_server_params, _mcp_tools_to_openai_schema
from agent.loop import _execute_tool

from mcp import ClientSession
from mcp.client.stdio import stdio_client


async def main():
    server_params = _load_mcp_server_params()

    print("Launching Alpaca MCP server...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("MCP session initialized!")

            tools_result = await session.list_tools()
            tools = _mcp_tools_to_openai_schema(tools_result.tools)

            print(f"Found {len(tools)} MCP tools.")

            # Deliberately request a stock order.
            order = {
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1",
                "type": "market",
                "time_in_force": "day",
            }

            print("\n" + "=" * 70)
            print("TESTING AI ORDER → SAFETY GATE")
            print("=" * 70)
            print(f"Requested tool: place_stock_order")
            print(f"Arguments: {order}")
            print("\nSending order request through agent safety layer...")

            result = await _execute_tool(
                session=session,
                tool_name="place_stock_order",
                arguments=order,
                settings=settings,
            )

            print("\n" + "=" * 70)
            print("SAFETY GATE RESULT")
            print("=" * 70)
            print(result)
            print("=" * 70)

            # Verify the order was blocked.
            try:
                data = json.loads(result)

                if data.get("blocked") is True:
                    print("\n✅ TEST PASSED")
                    print("The safety gate blocked the order.")
                    print("No order was sent to Alpaca.")
                else:
                    print("\n❌ TEST FAILED")
                    print("The order was not blocked.")

            except json.JSONDecodeError:
                print("\n❌ TEST FAILED")
                print("Safety gate returned an unexpected response.")


if __name__ == "__main__":
    asyncio.run(main())