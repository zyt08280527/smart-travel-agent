"""Start the place server and resolve two route endpoint names through MCP."""

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(
    origin_query: str,
    destination_query: str,
    limit: int,
) -> None:
    """Inspect and call the route endpoint resolution MCP tool once."""
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
            endpoint_tool = next(
                tool
                for tool in tools_result.tools
                if tool.name == "resolve_route_endpoints"
            )
            print("input schema:")
            print(json.dumps(endpoint_tool.inputSchema, ensure_ascii=False, indent=2))

            call_result = await session.call_tool(
                "resolve_route_endpoints",
                arguments={
                    "origin_query": origin_query,
                    "destination_query": destination_query,
                    "limit": limit,
                },
            )
            print("call result:")
            for content in call_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        print(text)
                    else:
                        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    """Parse command-line arguments and run the asynchronous smoke test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin-query", default="")
    parser.add_argument("--destination-query", default="")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(
        run(
            origin_query=args.origin_query,
            destination_query=args.destination_query,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
