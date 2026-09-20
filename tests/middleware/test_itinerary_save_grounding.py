import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.services.test_travel_replanning import build_payload
from travel_agent.middleware.itinerary_save_grounding import (
    ItinerarySaveGroundingMiddleware,
)


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


def test_save_selected_direct_transit_candidate_uses_candidate_two() -> None:
    payload = direct_transit_payload()
    payload["selected_transit_candidate_index"] = 1
    selected_message = AIMessage(
        content="已选择公共交通候选 2",
        additional_kwargs={"transit_route_payload": payload},
    )
    save_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "save_itinerary",
                "args": {
                    "title": "深大两校区行程",
                    "origin": "深圳大学粤海校区",
                    "destination": "深圳大学丽湖校区",
                    "travel_mode": "transit",
                    "distance_m": 15_000,
                    "duration_s": 4_800,
                    "duration_basis": "static_without_live_traffic",
                    "notes": "模型误用了候选1",
                },
                "id": "call-save",
                "type": "tool_call",
            }
        ],
    )

    result = ItinerarySaveGroundingMiddleware().after_model(
        {
            "messages": [
                HumanMessage(content="规划两校区公共交通"),
                ToolMessage(
                    name="plan_transit_route",
                    tool_call_id="call-transit",
                    content=json.dumps(direct_transit_payload(), ensure_ascii=False),
                ),
                selected_message,
                HumanMessage(content="保存当前路线"),
                save_message,
            ]
        },
        object(),
    )

    assert result is not None
    corrected = result["messages"][0].tool_calls[0]["args"]
    assert corrected["distance_m"] == 14_000
    assert corrected["duration_s"] == 4_200
    assert corrected["travel_mode"] == "transit"
    assert corrected["duration_basis"] == "static_without_live_traffic"
    assert "地铁7号线" in corrected["notes"]
    assert "换乘 2 次" in corrected["notes"]


def test_save_without_route_snapshot_is_not_modified() -> None:
    save_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "save_itinerary",
                "args": {"title": "没有路线"},
                "id": "call-save",
                "type": "tool_call",
            }
        ],
    )

    result = ItinerarySaveGroundingMiddleware().after_model(
        {"messages": [HumanMessage(content="保存"), save_message]},
        object(),
    )

    assert result is None


def test_save_uses_explicitly_selected_mode_instead_of_system_recommendation() -> None:
    payload = build_payload()
    payload["selected_mode"] = "walking"
    selected_message = AIMessage(
        content="已选择步行方案",
        additional_kwargs={"travel_planning_payload": payload},
    )
    save_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "save_itinerary",
                "args": {
                    "title": "深大两校区步行",
                    "origin": "错误起点",
                    "destination": "错误终点",
                    "travel_mode": "driving",
                    "distance_m": 1,
                    "duration_s": 1,
                    "duration_basis": "traffic_aware_estimate",
                },
                "id": "call-save",
                "type": "tool_call",
            }
        ],
    )

    result = ItinerarySaveGroundingMiddleware().after_model(
        {
            "messages": [
                selected_message,
                HumanMessage(content="保存当前选择的行程"),
                save_message,
            ]
        },
        object(),
    )

    assert result is not None
    corrected = result["messages"][0].tool_calls[0]["args"]
    assert corrected["origin"] == "粤海校区"
    assert corrected["destination"] == "丽湖校区"
    assert corrected["travel_mode"] == "walking"
    assert corrected["distance_m"] == 12_500
    assert corrected["duration_s"] == 9_000
    assert corrected["duration_basis"] == "static_without_live_traffic"


def test_save_planning_result_requires_explicit_mode_selection() -> None:
    payload = build_payload()
    payload["selected_mode"] = None
    route_message = AIMessage(
        content="已生成综合出行建议",
        additional_kwargs={"travel_planning_payload": payload},
    )
    save_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "save_itinerary",
                "args": {"title": "未确认的行程"},
                "id": "call-save",
                "type": "tool_call",
            }
        ],
    )

    result = ItinerarySaveGroundingMiddleware().after_model(
        {
            "messages": [
                route_message,
                HumanMessage(content="保存当前行程"),
                save_message,
            ]
        },
        object(),
    )

    assert result is not None
    response = result["messages"][0]
    assert response.tool_calls == []
    assert "先在规划卡片中选择最终出行方式" in response.content
