import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.services.test_travel_replanning import build_payload
from travel_agent.middleware.preference_replanning import (
    PreferenceReplanningMiddleware,
    parse_preference_updates,
)
from travel_agent.middleware.recommendation_explanation import (
    RecommendationExplanationMiddleware,
)


def previous_plan_messages() -> list[object]:
    return [
        HumanMessage(content="明天下午三点从粤海校区去丽湖校区"),
        ToolMessage(
            name="recommend_travel_plan",
            tool_call_id="call-plan",
            content=json.dumps(build_payload(), ensure_ascii=False),
        ),
        AIMessage(content="推荐驾车"),
    ]


def test_cannot_drive_follow_up_replans_without_model() -> None:
    result = PreferenceReplanningMiddleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果我不能开车呢？"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "end"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert "本轮没有重新请求外部服务" in str(response.content)
    assert "用户约束：不能驾车" in str(response.content)
    assert "推荐方式：公共交通" in str(response.content)
    payload = response.additional_kwargs["travel_planning_payload"]
    assert payload["context"]["preferences"]["can_drive"] is False


def test_first_turn_cannot_drive_request_is_not_intercepted() -> None:
    result = PreferenceReplanningMiddleware().before_model(
        {
            "messages": [
                HumanMessage(content="我不会开车，从粤海校区去丽湖校区")
            ]
        },
        object(),
    )

    assert result is None


def test_parse_supported_preference_updates() -> None:
    assert parse_preference_updates(
        "我不能开车，最多步行1.2公里，最多换乘一次，优先省钱"
    ) == {
        "can_drive": False,
        "max_walking_distance_m": 1200,
        "max_transfer_count": 1,
        "priority": "cheapest",
    }
    assert parse_preference_updates("步行不超过500米，时间优先") == {
        "max_walking_distance_m": 500,
        "priority": "fastest",
    }
    assert parse_preference_updates("尽量少换乘") == {
        "priority": "fewest_transfers"
    }


def test_walking_limit_follow_up_replans_previous_result() -> None:
    result = PreferenceReplanningMiddleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果最多步行500米呢？"),
            ]
        },
        object(),
    )

    assert result is not None
    response = result["messages"][0]
    assert "最大步行 500 米" in str(response.content)
    assert "本轮没有重新请求外部服务" in str(response.content)


def test_combined_constraints_return_actionable_rejection() -> None:
    result = PreferenceReplanningMiddleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="不能开车，最多步行100米，最多换乘0次"),
            ]
        },
        object(),
    )

    assert result is not None
    answer = str(result["messages"][0].content)
    assert "没有符合用户硬约束的出行方案" in answer
    assert "请放宽约束" in answer
    assert "本轮没有重新请求外部服务" in answer


def test_replanned_payload_can_be_explained_in_next_turn() -> None:
    replanned = PreferenceReplanningMiddleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果我不能开车呢？"),
            ]
        },
        object(),
    )
    assert replanned is not None

    explanation = RecommendationExplanationMiddleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果我不能开车呢？"),
                replanned["messages"][0],
                HumanMessage(content="为什么推荐这种方式？"),
            ]
        },
        object(),
    )

    assert explanation is not None
    assert "上次推荐公共交通" in str(explanation["messages"][0].content)
