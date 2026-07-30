"""Start the place server and exercise it through the MCP protocol."""

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(query: str, limit: int) -> None:
    """Start the place server, inspect its tool, and call it once."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "travel_agent.mcp_servers.place_server"],
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            print(f"server: {initialize_result.serverInfo.name}")

            tools_result = await session.list_tools()
            print(f"tools: {[tool.name for tool in tools_result.tools]}")
            place_tool = next(
                tool for tool in tools_result.tools if tool.name == "search_places"
            )
            print("input schema:")
            print(json.dumps(place_tool.inputSchema, ensure_ascii=False, indent=2))

            call_result = await session.call_tool(
                "search_places",
                arguments={"query": query, "limit": limit},
            )
            print("call result:")
            for content in call_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    print(text)


def main() -> None:
    """Parse command-line arguments and run the asynchronous smoke test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        default="",
        help="Place to search. Empty by default so the protocol test needs no network.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of place candidates to return.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.query, args.limit))


if __name__ == "__main__":
    main()
