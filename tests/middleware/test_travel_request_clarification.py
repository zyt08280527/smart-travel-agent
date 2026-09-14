from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from travel_agent.middleware.travel_request_clarification import (
    TravelRequestClarificationMiddleware,
)

REFERENCE_TIME = datetime(
    2026,
    9,
    12,
    10,
    0,
    tzinfo=ZoneInfo("Asia/Shanghai"),
)


def build_middleware() -> TravelRequestClarificationMiddleware:
    return TravelRequestClarificationMiddleware(
        clock=lambda: REFERENCE_TIME,
    )


def test_date_only_travel_request_is_stopped_before_model() -> None:
    result = build_middleware().before_model(
        {
            "messages": [
                HumanMessage(
                    content="明天从深圳大学粤海校区去深圳大学丽湖校区"
                )
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "end"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert "几点出发" in str(response.content)


def test_exact_departure_time_continues_to_model() -> None:
    result = build_middleware().before_model(
        {
            "messages": [
                HumanMessage(
                    content="明天下午三点从深圳大学粤海校区去丽湖校区"
                )
            ]
        },
        object(),
    )

    assert result is None


def test_date_only_arrival_goal_asks_for_exact_deadline() -> None:
    result = build_middleware().before_model(
        {"messages": [HumanMessage(content="明天前到达深圳市民中心")]},
        object(),
    )

    assert result is not None
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert "最晚到达时间" in str(response.content)


def test_exact_arrival_goal_continues_to_model() -> None:
    result = build_middleware().before_model(
        {"messages": [HumanMessage(content="明天上午9点前到达深圳市民中心")]},
        object(),
    )

    assert result is None


def test_non_travel_date_request_is_not_intercepted() -> None:
    result = build_middleware().before_model(
        {"messages": [HumanMessage(content="明天深圳天气怎么样？")]},
        object(),
    )

    assert result is None


def test_follow_up_time_keeps_existing_conversation_for_model() -> None:
    result = build_middleware().before_model(
        {
            "messages": [
                HumanMessage(content="明天从粤海校区去丽湖校区"),
                AIMessage(content="请问几点出发？"),
                HumanMessage(content="下午三点"),
            ]
        },
        object(),
    )

    assert result is None


def test_hook_does_not_repeat_after_model_or_tool_activity() -> None:
    result = build_middleware().before_model(
        {
            "messages": [
                HumanMessage(content="明天从粤海校区去丽湖校区"),
                AIMessage(content="正在处理。"),
            ]
        },
        object(),
    )

    assert result is None


@pytest.mark.asyncio
async def test_middleware_can_end_a_real_agent_graph_before_model() -> None:
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="不应该调用模型")]
    )
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[build_middleware()],
    )

    result = await agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "明天从粤海校区去丽湖校区",
                }
            ]
        }
    )

    final_message = result["messages"][-1]
    assert isinstance(final_message, AIMessage)
    assert "几点出发" in str(final_message.content)
    assert "不应该调用模型" not in str(final_message.content)
