import asyncio

from main import _load_mcp_server_params
from agent.loop import _mcp_result_to_text

from mcp import ClientSession
from mcp.client.stdio import stdio_client


async def main():
    server_params = _load_mcp_server_params()

    print("Launching Alpaca MCP server...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("MCP session initialized!")

            result = await session.call_tool(
                "get_all_positions",
                {},
            )

            print("\n" + "=" * 70)
            print("RAW MCP POSITION RESULT")
            print("=" * 70)
            print(result)

            print("\n" + "=" * 70)
            print("CONVERTED TEXT")
            print("=" * 70)

            text = _mcp_result_to_text(result)

            print(repr(text))

            print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(main())