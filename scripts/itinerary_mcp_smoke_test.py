"""Inspect itinerary MCP read/write tools without creating a record."""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run() -> None:
    """Read saved trips and verify invalid writes are rejected."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "travel_agent.mcp_servers.itinerary_server"],
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            print(f"server: {initialize_result.serverInfo.name}")

            tools_result = await session.list_tools()
            print(f"tools: {[tool.name for tool in tools_result.tools]}")
            save_tool = next(
                tool for tool in tools_result.tools if tool.name == "save_itinerary"
            )
            print("input schema:")
            print(json.dumps(save_tool.inputSchema, ensure_ascii=False, indent=2))

            list_result = await session.call_tool(
                "list_itineraries",
                arguments={"limit": 1},
            )
            print("list result:")
            for content in list_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    print(text)

            call_result = await session.call_tool(
                "save_itinerary",
                arguments={
                    "title": "",
                    "origin": "起点",
                    "destination": "终点",
                    "travel_mode": "driving",
                    "distance_m": -1,
                    "duration_s": 10,
                    "duration_basis": "static_without_live_traffic",
                },
            )
            print("call result:")
            for content in call_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    print(text)


def main() -> None:
    """Run the asynchronous MCP protocol smoke test."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
