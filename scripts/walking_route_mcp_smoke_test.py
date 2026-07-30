"""Start the route MCP server and call its walking route tool."""

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def print_tool_result(text: str) -> None:
    """Print a compact walking route summary or a complete error payload."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print(text)
        return

    if not isinstance(payload, dict) or "distance_m" not in payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    summary = {
        "mode": payload["mode"],
        "origin": payload["origin"],
        "destination": payload["destination"],
        "distance_m": payload["distance_m"],
        "duration_s": payload["duration_s"],
        "step_count": payload["step_count"],
        "first_5_steps": payload["steps"][:5],
        "geometry_point_count": payload["geometry_point_count"],
        "attribution": payload["attribution"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


async def run(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> None:
    """Inspect and call the walking route MCP tool once."""
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
            walking_tool = next(
                tool
                for tool in tools_result.tools
                if tool.name == "plan_walking_route"
            )
            print("input schema:")
            print(json.dumps(walking_tool.inputSchema, ensure_ascii=False, indent=2))

            call_result = await session.call_tool(
                "plan_walking_route",
                arguments={
                    "origin_latitude": origin_latitude,
                    "origin_longitude": origin_longitude,
                    "destination_latitude": destination_latitude,
                    "destination_longitude": destination_longitude,
                },
            )
            print("call result:")
            for content in call_result.content:
                text = getattr(content, "text", None)
                if text is not None:
                    print_tool_result(text)


def main() -> None:
    """Parse coordinates and run the asynchronous MCP smoke test."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin-latitude", type=float, default=22.5359023)
    parser.add_argument("--origin-longitude", type=float, default=113.9314749)
    parser.add_argument("--destination-latitude", type=float, default=22.6009872)
    parser.add_argument("--destination-longitude", type=float, default=113.987959)
    args = parser.parse_args()
    asyncio.run(
        run(
            origin_latitude=args.origin_latitude,
            origin_longitude=args.origin_longitude,
            destination_latitude=args.destination_latitude,
            destination_longitude=args.destination_longitude,
        )
    )


if __name__ == "__main__":
    main()
