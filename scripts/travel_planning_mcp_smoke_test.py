"""Start the planning MCP server and request one real multi-mode comparison."""

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _summary(payload: dict[str, object]) -> dict[str, object]:
    if "recommendation" not in payload:
        return payload

    context = payload["context"]
    recommendation = payload["recommendation"]
    assert isinstance(context, dict)
    assert isinstance(recommendation, dict)
    ranked_options = recommendation["ranked_options"]
    assert isinstance(ranked_options, list)
    return {
        "city": context["city"],
        "weather": context.get("weather"),
        "recommended_mode": recommendation["recommended_mode"],
        "confidence": recommendation["confidence"],
        "ranked_options": [
            {
                "mode": item["option"]["mode"],
                "status": item["option"]["status"],
                "distance_m": item["option"]["distance_m"],
                "duration_s": item["option"]["duration_s"],
                "cost_yuan": item["option"].get("cost_yuan"),
                "walking_distance_m": item["option"].get(
                    "walking_distance_m"
                ),
                "transfer_count": item["option"].get("transfer_count"),
                "total_score": item["scores"]["total"],
                "attribution": item["option"]["attribution"],
            }
            for item in ranked_options
        ],
        "unavailable_options": recommendation["unavailable_options"],
        "summary_reasons": recommendation["summary_reasons"],
        "limitations": recommendation["limitations"],
    }


async def run(args: argparse.Namespace) -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "travel_agent.mcp_servers.planning_server"],
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialize_result = await session.initialize()
            print(f"server: {initialize_result.serverInfo.name}")
            tools_result = await session.list_tools()
            print(f"tools: {[tool.name for tool in tools_result.tools]}")

            result = await session.call_tool(
                "recommend_travel_plan",
                arguments={
                    "city": args.city,
                    "origin_name": args.origin_name,
                    "destination_name": args.destination_name,
                    "origin_latitude": args.origin_latitude,
                    "origin_longitude": args.origin_longitude,
                    "destination_latitude": args.destination_latitude,
                    "destination_longitude": args.destination_longitude,
                    "priority": args.priority,
                },
            )
            print("call metadata:")
            print(json.dumps(result.meta, ensure_ascii=False, indent=2))
            print("call result:")
            for content in result.content:
                text = getattr(content, "text", None)
                if text is None:
                    continue
                payload = json.loads(text)
                print(
                    json.dumps(
                        _summary(payload),
                        ensure_ascii=False,
                        indent=2,
                    )
                )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--origin-name", default="深圳大学粤海校区")
    parser.add_argument("--destination-name", default="深圳大学丽湖校区")
    parser.add_argument("--origin-latitude", type=float, default=22.5359023)
    parser.add_argument("--origin-longitude", type=float, default=113.9314749)
    parser.add_argument("--destination-latitude", type=float, default=22.6009872)
    parser.add_argument("--destination-longitude", type=float, default=113.987959)
    parser.add_argument(
        "--priority",
        choices=[
            "balanced",
            "fastest",
            "cheapest",
            "least_walking",
            "fewest_transfers",
        ],
        default="balanced",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
