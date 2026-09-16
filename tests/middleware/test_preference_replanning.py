import json
from datetime import timedelta

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.services.test_travel_replanning import SNAPSHOT_AT, build_payload
from travel_agent.middleware.preference_replanning import (
    PreferenceReplanningMiddleware,
    parse_preference_updates,
    parse_transit_candidate_selection,
)
from travel_agent.middleware.recommendation_explanation import (
    RecommendationExplanationMiddleware,
)


def replanning_middleware(*, age: timedelta = timedelta(minutes=1)):
    return PreferenceReplanningMiddleware(clock=lambda: SNAPSHOT_AT + age)


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


def previous_plan_messages_with_transit_candidates() -> list[object]:
    payload = build_payload()
    payload["transit_candidates"] = [
        {
            "distance_m": 8_000,
            "duration_s": 3_000,
            "walking_distance_m": 300,
            "cost_yuan": 4,
            "transfer_count": 0,
            "legs": [{"mode": "walking", "distance_m": 300}],
        },
        {
            "distance_m": 9_000,
            "duration_s": 1_800,
            "walking_distance_m": 900,
            "cost_yuan": 7,
            "transfer_count": 2,
            "legs": [{"mode": "walking", "distance_m": 900}],
        },
    ]
    payload["selected_transit_candidate_index"] = 1
    return [
        HumanMessage(content="从粤海校区去丽湖校区"),
        ToolMessage(
            name="recommend_travel_plan",
            tool_call_id="call-plan",
            content=json.dumps(payload, ensure_ascii=False),
        ),
        AIMessage(content="推荐驾车"),
    ]


def direct_transit_payload() -> dict[str, object]:
    return {
        "mode": "transit",
        "origin": {"latitude": 22.53, "longitude": 113.93},
        "destination": {"latitude": 22.59, "longitude": 113.99},
        "origin_city_code": "0755",
        "destination_city_code": "0755",
        "strategy": 7,
        "options": [
            {
                "distance_m": 15_000,
                "duration_s": 4_800,
                "walking_distance_m": 900,
                "cost_yuan": 4,
                "transfer_count": 1,
                "legs": [
                    {
                        "mode": "subway",
                        "distance_m": 14_100,
                        "duration_s": 3_900,
                        "line_name": "地铁5号线",
                    }
                ],
            },
            {
                "distance_m": 14_000,
                "duration_s": 4_200,
                "walking_distance_m": 500,
                "cost_yuan": 5,
                "transfer_count": 2,
                "legs": [
                    {
                        "mode": "subway",
                        "distance_m": 13_500,
                        "duration_s": 3_700,
                        "line_name": "地铁7号线",
                    }
                ],
            },
        ],
        "attribution": "公交路线数据来源：高德地图 Web服务 API",
    }


def test_cannot_drive_follow_up_replans_without_model() -> None:
    result = replanning_middleware().before_model(
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
    result = replanning_middleware().before_model(
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
    assert parse_preference_updates("公共交通改为地铁优先") == {
        "transit_strategy": "subway_first"
    }
    assert parse_preference_updates("公共交通改为少换乘") == {
        "transit_strategy": "fewest_transfers"
    }
    assert parse_preference_updates("公共交通改为少步行") == {
        "transit_strategy": "least_walking"
    }
    assert parse_transit_candidate_selection("选择公共交通候选2") == 1
    assert parse_transit_candidate_selection("切换到候选 1") == 0
    assert parse_transit_candidate_selection("候选0") is None


def test_concrete_transit_candidate_selection_updates_snapshot_locally() -> None:
    result = replanning_middleware().before_model(
        {
            "messages": [
                *previous_plan_messages_with_transit_candidates(),
                HumanMessage(content="选择公共交通候选1"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "end"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert "本轮没有重新请求外部服务" in str(response.content)
    payload = response.additional_kwargs["travel_planning_payload"]
    assert payload["selected_transit_candidate_index"] == 0
    transit = next(
        scored["option"]
        for scored in payload["recommendation"]["ranked_options"]
        if scored["option"]["mode"] == "transit"
    )
    assert transit["duration_s"] == 3_000
    assert response.additional_kwargs["travel_planning_update"] == {
        "reused_previous_data": True,
        "manual_transit_selection": True,
        "previous_recommended_mode": "driving",
    }


def test_direct_transit_candidate_selection_updates_latest_route_locally() -> None:
    result = replanning_middleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                ToolMessage(
                    name="plan_transit_route",
                    tool_call_id="call-transit",
                    content=json.dumps(direct_transit_payload(), ensure_ascii=False),
                ),
                AIMessage(content="已规划公共交通路线"),
                HumanMessage(content="选择公共交通候选2"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "end"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert "地铁7号线" in str(response.content)
    assert "没有重新请求外部服务" in str(response.content)
    payload = response.additional_kwargs["transit_route_payload"]
    assert payload["selected_transit_candidate_index"] == 1


def test_expired_transit_candidates_cannot_be_selected() -> None:
    result = replanning_middleware(age=timedelta(minutes=5)).before_model(
        {
            "messages": [
                *previous_plan_messages_with_transit_candidates(),
                HumanMessage(content="选择公共交通候选1"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "end"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert "候选已经过期" in str(response.content)
    assert "travel_planning_payload" not in response.additional_kwargs


def test_walking_limit_follow_up_replans_previous_result() -> None:
    result = replanning_middleware().before_model(
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
    result = replanning_middleware().before_model(
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
    replanned = replanning_middleware().before_model(
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


def test_expired_snapshot_triggers_deterministic_route_refresh() -> None:
    result = replanning_middleware(age=timedelta(minutes=5)).before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果我不能开车呢？"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "tools"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    assert len(response.tool_calls) == 1
    tool_call = response.tool_calls[0]
    assert tool_call["name"] == "recommend_travel_plan"
    assert tool_call["args"]["origin_name"] == "粤海校区"
    assert tool_call["args"]["destination_name"] == "丽湖校区"
    assert tool_call["args"]["can_drive"] is False
    assert response.additional_kwargs["travel_planning_update"] == {
        "reused_previous_data": False,
        "refresh_reason": "route_snapshot_expired",
    }


def test_public_transit_strategy_change_refreshes_even_fresh_snapshot() -> None:
    result = replanning_middleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="公共交通改为地铁优先"),
            ]
        },
        object(),
    )

    assert result is not None
    assert result["jump_to"] == "tools"
    response = result["messages"][0]
    assert isinstance(response, AIMessage)
    tool_call = response.tool_calls[0]
    assert tool_call["name"] == "recommend_travel_plan"
    assert tool_call["args"]["transit_strategy"] == "subway_first"
    assert response.additional_kwargs["travel_planning_update"] == {
        "reused_previous_data": False,
        "refresh_reason": "transit_strategy_changed",
    }


def test_failed_transit_strategy_refresh_does_not_relabel_old_candidates() -> None:
    refresh = replanning_middleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="公共交通改为地铁优先"),
            ]
        },
        object(),
    )
    assert refresh is not None
    request_message = refresh["messages"][0]
    assert isinstance(request_message, AIMessage)
    tool_call_id = request_message.tool_calls[0]["id"]

    fallback = replanning_middleware().before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="公共交通改为地铁优先"),
                request_message,
                ToolMessage(
                    name="recommend_travel_plan",
                    tool_call_id=tool_call_id,
                    content='{"ok":false,"error":"路线服务暂时不可用"}',
                ),
            ]
        },
        object(),
    )

    assert fallback is not None
    response = fallback["messages"][0]
    assert isinstance(response, AIMessage)
    assert "已保留上一轮路线" in str(response.content)
    assert "travel_planning_payload" not in response.additional_kwargs


def test_failed_refresh_falls_back_to_expired_snapshot() -> None:
    refresh = replanning_middleware(age=timedelta(minutes=5)).before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果我不能开车呢？"),
            ]
        },
        object(),
    )
    assert refresh is not None
    request_message = refresh["messages"][0]
    assert isinstance(request_message, AIMessage)
    tool_call_id = request_message.tool_calls[0]["id"]

    fallback = replanning_middleware(age=timedelta(minutes=5)).before_model(
        {
            "messages": [
                *previous_plan_messages(),
                HumanMessage(content="如果我不能开车呢？"),
                request_message,
                ToolMessage(
                    name="recommend_travel_plan",
                    tool_call_id=tool_call_id,
                    content='{"ok":false,"error":"路线服务暂时不可用"}',
                ),
            ]
        },
        object(),
    )

    assert fallback is not None
    assert fallback["jump_to"] == "end"
    response = fallback["messages"][0]
    assert isinstance(response, AIMessage)
    assert "已过期" in str(response.content)
    assert "仅供临时参考" in str(response.content)
    payload = response.additional_kwargs["travel_planning_payload"]
    assert payload["context"]["preferences"]["can_drive"] is False
    assert payload["recommendation"]["recommended_mode"] == "transit"
    assert response.additional_kwargs["travel_planning_update"] == {
        "reused_previous_data": True,
        "used_stale_snapshot": True,
        "refresh_failed": True,
        "previous_recommended_mode": "driving",
    }


def test_successful_refresh_is_not_intercepted_as_fallback() -> None:
    result = replanning_middleware().before_model(
        {
            "messages": [
                ToolMessage(
                    name="recommend_travel_plan",
                    tool_call_id="refresh-plan-123",
                    content=json.dumps(build_payload(), ensure_ascii=False),
                )
            ]
        },
        object(),
    )

    assert result is None


def test_expired_legacy_payload_without_coordinates_falls_back_to_agent() -> None:
    payload = build_payload()
    context = payload["context"]
    assert isinstance(context, dict)
    context.pop("origin")
    context.pop("destination")
    messages = previous_plan_messages()
    tool_message = messages[1]
    assert isinstance(tool_message, ToolMessage)
    tool_message.content = json.dumps(payload, ensure_ascii=False)

    result = replanning_middleware(age=timedelta(minutes=5)).before_model(
        {
            "messages": [
                *messages,
                HumanMessage(content="如果我不能开车呢？"),
            ]
        },
        object(),
    )

    assert result is None
