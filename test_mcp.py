import asyncio
from main import _load_mcp_server_params, _mcp_tools_to_anthropic_schema
from mcp import ClientSession
from mcp.client.stdio import stdio_client

async def test():
    server_params = _load_mcp_server_params()
    print("Loaded MCP server params, launching server...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("MCP session initialized!")

            tools_result = await session.list_tools()
            anthropic_tools = _mcp_tools_to_anthropic_schema(tools_result.tools)

            print(f"\nFound {len(anthropic_tools)} tools:\n")
            for tool in anthropic_tools:
                print(f"  - {tool['name']}: {tool['description'][:80]}")

asyncio.run(test())