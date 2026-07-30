"""Start the route MCP server and call its transit route tool."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    """Inspect and call the transit route MCP tool once."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "travel_agent.mcp_servers.route_server"],
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            print(f"server: {initialize_result.serverInfo.name}")

            tools_result = await session.list_tools()
            print(f"tools: {[tool.name for tool in tools_result.tools]}")
            transit_tool = next(
                tool
                for tool in tools_result.tools
                if tool.name == "plan_transit_route"
            )
            print("input schema:")
            print(
                json.dumps(
                    transit_tool.inputSchema,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            call_result = await session.call_tool(
                "plan_transit_route",
                arguments={
                    "origin_latitude": 22.5359023,
                    "origin_longitude": 113.9314749,
                    "destination_latitude": 22.6009872,
                    "destination_longitude": 113.987959,
                    "strategy": 0,
                    "alternative_routes": 3,
                },
            )
            print("call result:")
            for content in call_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    payload = json.loads(text)
                    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
