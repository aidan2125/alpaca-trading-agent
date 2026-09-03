import asyncio

from main import _load_mcp_server_params, _mcp_tools_to_openai_schema
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from config import settings
from agent.loop import run_cycle


async def test():
    server_params = _load_mcp_server_params()

    print("Launching Alpaca MCP server...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("MCP session initialized!")

            tools_result = await session.list_tools()

            openai_tools = _mcp_tools_to_openai_schema(
                tools_result.tools
            )

            print(f"Found {len(openai_tools)} MCP tools.")
            print("Starting one GLM-5.2 agent cycle...\n")

            await run_cycle(
                session=session,
                tools=openai_tools,
                settings=settings,
            )


if __name__ == "__main__":
    asyncio.run(test())