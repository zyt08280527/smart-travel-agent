from typing import Any
from unittest.mock import Mock

import pytest
from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from travel_agent.middleware.arrival_deadline_routing import (
    ArrivalDeadlineRoutingMiddleware,
)


@tool
def resolve_route_endpoints(origin_query: str, destination_query: str) -> str:
    """Resolve route endpoint candidates."""
    return f"{origin_query}->{destination_query}"


@tool
def recommend_travel_plan(city: str) -> str:
    """Recommend one composite travel plan."""
    return city


@tool
def plan_transit_route() -> str:
    """Plan one transit route."""
    return "transit"


async def capture_request(request: ModelRequest[Any]) -> Any:
    return request


async def capture_tool_request(request: ToolCallRequest) -> Any:
    return request


def build_request(messages: list[object]) -> ModelRequest[Any]:
    return ModelRequest(
        model=Mock(),
        messages=messages,  # type: ignore[arg-type]
        tools=[resolve_route_endpoints, recommend_travel_plan, plan_transit_route],
        runtime=Mock(),
    )


@pytest.mark.asyncio
async def test_arrival_request_first_forces_endpoint_resolution() -> None:
    result = await ArrivalDeadlineRoutingMiddleware().awrap_model_call(
        build_request(
            [HumanMessage(content="明天9点前从深圳北站到市民中心")]
        ),
        capture_request,
    )

    assert result.tool_choice == "resolve_route_endpoints"
    assert [tool.name for tool in result.tools] == ["resolve_route_endpoints"]


@pytest.mark.asyncio
async def test_arrival_request_then_forces_composite_planning() -> None:
    result = await ArrivalDeadlineRoutingMiddleware().awrap_model_call(
        build_request(
            [
                HumanMessage(content="明天9点前从深圳北站到市民中心"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "resolve_route_endpoints",
                            "args": {},
                            "id": "call-endpoints",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content='{"origin":{},"destination":{}}',
                    name="resolve_route_endpoints",
                    tool_call_id="call-endpoints",
                ),
            ]
        ),
        capture_request,
    )

    assert result.tool_choice == "recommend_travel_plan"
    assert [tool.name for tool in result.tools] == ["recommend_travel_plan"]


@pytest.mark.asyncio
async def test_after_composite_plan_model_can_answer_normally() -> None:
    request = build_request(
        [
            HumanMessage(content="明天9点前从深圳北站到市民中心"),
            ToolMessage(
                content='{"context":{},"recommendation":{}}',
                name="recommend_travel_plan",
                tool_call_id="call-plan",
            ),
        ]
    )
    result = await ArrivalDeadlineRoutingMiddleware().awrap_model_call(
        request,
        capture_request,
    )

    assert result is request
    assert result.tool_choice is None


@pytest.mark.asyncio
async def test_unrelated_request_keeps_all_tools_available() -> None:
    request = build_request([HumanMessage(content="深圳现在天气如何")])
    result = await ArrivalDeadlineRoutingMiddleware().awrap_model_call(
        request,
        capture_request,
    )

    assert result is request
    assert len(result.tools) == 3


@pytest.mark.asyncio
async def test_planning_call_repairs_arrival_text_misclassified_as_departure() -> None:
    request = ToolCallRequest(
        tool_call={
            "name": "recommend_travel_plan",
            "args": {
                "city": "深圳",
                "departure_time_text": "明天上午9点前",
            },
            "id": "call-plan",
            "type": "tool_call",
        },
        tool=recommend_travel_plan,
        state={
            "messages": [
                HumanMessage(
                    content=(
                        "请规划明天上午9点前从深圳北站到深圳市民中心，"
                        "不能开车。"
                    )
                )
            ]
        },
        runtime=Mock(),
    )

    result = await ArrivalDeadlineRoutingMiddleware().awrap_tool_call(
        request,
        capture_tool_request,
    )

    args = result.tool_call["args"]
    assert args["departure_time_text"] is None
    assert args["arrival_time_text"].startswith("请规划明天上午9点前")
    assert args["arrival_buffer_minutes"] == 15
