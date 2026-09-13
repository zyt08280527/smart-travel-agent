import pytest
from langchain.agents import create_agent
from langchain_core.language_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from travel_agent.middleware.recommendation_explanation import (
    RecommendationExplanationMiddleware,
    render_recommendation_explanation,
)

PLANNING_PAYLOAD = {
    "context": {
        "preferences": {"priority": "balanced"},
        "weather": {
            "condition": "小雨",
            "forecast_at": "2026-09-13T15:00",
        },
    },
    "recommendation": {
        "recommended_mode": "driving",
        "confidence": 0.55,
        "ranked_options": [
            {
                "option": {
                    "mode": "driving",
                    "duration_s": 1800,
                },
                "scores": {"total": 82, "weather_fit": 95},
            },
            {
                "option": {
                    "mode": "transit",
                    "duration_s": 2700,
                    "walking_distance_m": 800,
                    "transfer_count": 1,
                },
                "scores": {"total": 77, "weather_fit": 78},
            },
        ],
        "unavailable_options": [],
    },
}


def planning_tool_message() -> ToolMessage:
    return ToolMessage(
        name="recommend_travel_plan",
        tool_call_id="call-plan",
        content=(
            '{"context":{"preferences":{"priority":"balanced"},'
            '"weather":{"condition":"小雨",'
            '"forecast_at":"2026-09-13T15:00"}},'
            '"recommendation":{"recommended_mode":"driving",'
            '"confidence":0.55,"ranked_options":['
            '{"option":{"mode":"driving","duration_s":1800},'
            '"scores":{"total":82,"weather_fit":95}},'
            '{"option":{"mode":"transit","duration_s":2700,'
            '"walking_distance_m":800,"transfer_count":1},'
            '"scores":{"total":77,"weather_fit":78}}],'
            '"unavailable_options":[]}}'
        ),
    )


def test_render_explanation_uses_structured_scores_and_route_facts() -> None:
    answer = render_recommendation_explanation(PLANNING_PAYLOAD)

    assert answer is not None
    assert "推荐驾车" in answer
    assert "规则评分" in answer
    assert "综合得分为 82.0" in answer
    assert "公共交通" in answer
    assert "领先 5.0 分" in answer
    assert "少 15 分钟" in answer
    assert "出发时段预报为小雨" in answer


def test_explanation_follow_up_is_stopped_before_model() -> None:
    middleware = RecommendationExplanationMiddleware()
    result = middleware.before_model(
        {
            "messages": [
                HumanMessage(content="帮我比较路线"),
                planning_tool_message(),
                AIMessage(content="推荐驾车"),
                HumanMessage(content="为什么推荐这种方式？"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "end"
    assert "综合得分为 82.0" in str(result["messages"][0].content)


def test_explanation_without_previous_plan_has_safe_response() -> None:
    result = RecommendationExplanationMiddleware().before_model(
        {"messages": [HumanMessage(content="为什么推荐这种方式？")]},
        object(),
    )

    assert result is not None
    assert "还没有可解释" in str(result["messages"][0].content)


def test_unrelated_follow_up_continues_to_model() -> None:
    result = RecommendationExplanationMiddleware().before_model(
        {"messages": [HumanMessage(content="请展开公交路线详情")]},
        object(),
    )

    assert result is None


@pytest.mark.asyncio
async def test_middleware_bypasses_model_in_real_agent_graph() -> None:
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="不应该调用模型")]
    )
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[RecommendationExplanationMiddleware()],
    )

    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(content="帮我比较路线"),
                planning_tool_message(),
                AIMessage(content="推荐驾车"),
                HumanMessage(content="为什么推荐这种方式？"),
            ]
        }
    )

    answer = str(result["messages"][-1].content)
    assert "规则评分" in answer
    assert "不应该调用模型" not in answer
