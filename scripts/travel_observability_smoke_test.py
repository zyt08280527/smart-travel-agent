"""Trace hidden HTTP observations for place and route MCP tools."""

import asyncio
import json
from typing import Any

from langchain.messages import ToolMessage

from travel_agent.agent import travel_agent_session

ORIGIN = {"latitude": 22.5359023, "longitude": 113.9314749}
DESTINATION = {"latitude": 22.6009872, "longitude": 113.987959}


async def _invoke_tool(
    tools: list[Any],
    name: str,
    args: dict[str, Any],
) -> ToolMessage:
    tool = next(tool for tool in tools if tool.name == name)
    result = await tool.ainvoke(
        {
            "type": "tool_call",
            "name": name,
            "args": args,
            "id": f"{name}-observability-smoke-test",
        }
    )
    if not isinstance(result, ToolMessage):
        raise TypeError(f"{name} 应返回包含 artifact 的 ToolMessage")
    return result


def _print_observability(name: str, result: ToolMessage) -> None:
    observability = _get_observability(result)
    print(f"\n========== {name} ==========")
    print(json.dumps(observability, ensure_ascii=False, indent=2))


def _get_observability(result: ToolMessage) -> dict[str, Any]:
    artifact = result.artifact
    if not isinstance(artifact, dict):
        return {}
    structured_content = artifact.get("structured_content")
    if not isinstance(structured_content, dict):
        return {}
    observability = structured_content.get("observability")
    return observability if isinstance(observability, dict) else {}


async def main() -> None:
    """Call each networked tool directly without invoking the language model."""
    coordinate_args = {
        "origin_latitude": ORIGIN["latitude"],
        "origin_longitude": ORIGIN["longitude"],
        "destination_latitude": DESTINATION["latitude"],
        "destination_longitude": DESTINATION["longitude"],
    }
    cases = [
        ("search_places", {"query": "深圳大学粤海校区", "limit": 1}),
        (
            "resolve_route_endpoints",
            {
                "origin_query": "深圳大学粤海校区",
                "destination_query": "深圳大学丽湖校区",
                "limit": 1,
            },
        ),
        ("plan_driving_route", coordinate_args),
        ("plan_walking_route", coordinate_args),
        ("plan_transit_route", coordinate_args),
    ]

    total_requests = 0
    async with travel_agent_session() as (_agent, tools):
        for name, args in cases:
            result = await _invoke_tool(tools, name, args)
            _print_observability(name, result)
            observability = _get_observability(result)
            total_requests += observability.get(
                "external_http_request_count",
                0,
            )

    print("\n========== 汇总 ==========")
    print(f"工具调用数: {len(cases)}")
    print(f"外部 HTTP 请求数: {total_requests}")
    print("本脚本未调用大模型，因此模型调用数和 Token 消耗均为 0。")


if __name__ == "__main__":
    asyncio.run(main())
