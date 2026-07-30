"""Start the weather server and exercise it through the MCP protocol."""

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(city: str) -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "travel_agent.mcp_servers.weather_server"],
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            print(f"server: {initialize_result.serverInfo.name}")

            tools_result = await session.list_tools()
            print(f"tools: {[tool.name for tool in tools_result.tools]}")
            weather_tool = next(
                tool for tool in tools_result.tools if tool.name == "query_current_weather"
            )
            print("input schema:")
            print(json.dumps(weather_tool.inputSchema, ensure_ascii=False, indent=2))

            call_result = await session.call_tool(
                "query_current_weather",
                arguments={"city": city},
            )
            print("call result:")
            for content in call_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--city",
        default="",
        help="City to query. Empty by default so the protocol test needs no network.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.city))


if __name__ == "__main__":
    main()

