import json
from uuid import UUID

import pytest
from langchain.messages import AIMessage, AIMessageChunk, ToolMessage

from tests.services.test_travel_replanning import build_payload
from travel_agent.api.streaming import normalize_stream_part

THREAD_ID = UUID("12345678-1234-5678-1234-567812345678")


def test_message_chunk_becomes_assistant_delta() -> None:
    events = normalize_stream_part(
        {
            "type": "messages",
            "data": (
                AIMessageChunk(content="深圳当前天气"),
                {"langgraph_node": "model"},
            ),
        },
        THREAD_ID,
    )

    assert len(events) == 1
    assert events[0].type == "assistant_delta"
    assert events[0].delta == "深圳当前天气"


def test_empty_and_tool_message_chunks_are_not_forwarded() -> None:
    empty_events = normalize_stream_part(
        {
            "type": "messages",
            "data": (
                AIMessageChunk(content=""),
                {"langgraph_node": "model"},
            ),
        },
        THREAD_ID,
    )
    tool_events = normalize_stream_part(
        {
            "type": "messages",
            "data": (
                ToolMessage(
                    content="天气工具原始结果",
                    name="query_current_weather",
                    tool_call_id="call_weather",
                ),
                {"langgraph_node": "tools"},
            ),
        },
        THREAD_ID,
    )

    assert empty_events == []
    assert tool_events == []


def test_complete_tool_call_becomes_tool_requested() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "query_current_weather",
                                    "args": {"city": "深圳"},
                                    "id": "call_weather",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert len(events) == 1
    assert events[0].type == "tool_requested"
    assert events[0].tool_name == "query_current_weather"
    assert events[0].tool_args == {"city": "深圳"}
    assert events[0].tool_call_id == "call_weather"


def test_tool_message_becomes_tool_completed_without_raw_result() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="包含大量原始天气JSON",
                            name="query_current_weather",
                            tool_call_id="call_weather",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert len(events) == 1
    assert events[0].type == "tool_completed"
    assert events[0].tool_name == "query_current_weather"
    assert events[0].tool_call_id == "call_weather"
    assert events[0].model_dump().get("tool_result") is None


def test_weather_tool_result_becomes_safe_structured_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=[
                                {
                                    "type": "text",
                                    "text": (
                                        '{"location":{"name":"深圳",'
                                        '"country":"中国","admin1":"广东"},'
                                        '"temperature_c":25.1,'
                                        '"apparent_temperature_c":29.4,'
                                        '"precipitation_mm":0.0,'
                                        '"wind_speed_kmh":10.7,'
                                        '"weather_code":3,'
                                        '"condition":"阴天",'
                                        '"observed_at":"2026-07-28T21:30"}'
                                    ),
                                }
                            ],
                            name="query_current_weather",
                            tool_call_id="call_weather",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == [
        "tool_completed",
        "result_card",
    ]
    card = events[1].card
    assert card is not None
    assert card.type == "weather"
    assert card.city == "深圳"
    assert card.temperature_c == 25.1
    assert card.apparent_temperature_c == 29.4
    assert card.precipitation_mm == 0.0
    assert card.condition == "阴天"


@pytest.mark.parametrize(
    ("tool_name", "mode"),
    [
        ("plan_driving_route", "driving"),
        ("plan_walking_route", "walking"),
    ],
)
def test_route_tool_result_becomes_safe_structured_card(
    tool_name: str,
    mode: str,
) -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=(
                                f'{{"mode":"{mode}",'
                                '"distance_m":13709.7,'
                                '"duration_s":9870.9,'
                                '"steps":[],"step_count":61,'
                                '"attribution":"Routing test attribution"}'
                            ),
                            name=tool_name,
                            tool_call_id="call_route",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == [
        "tool_completed",
        "result_card",
    ]
    card = events[1].card
    assert card is not None
    assert card.type == "route"
    assert card.mode == mode
    assert card.distance_m == 13709.7
    assert card.duration_s == 9870.9
    assert card.step_count == 61


def test_driving_route_card_exposes_traffic_and_cost_summary() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=(
                                '{"mode":"driving","distance_m":14869,'
                                '"duration_s":1687,'
                                '"duration_basis":"traffic_aware_estimate",'
                                '"tolls_yuan":0,"taxi_cost_yuan":38,'
                                '"traffic_lights":4,"restriction":0,'
                                '"traffic_segments":['
                                '{"status":"畅通","distance_m":1000},'
                                '{"status":"畅通","distance_m":800},'
                                '{"status":"缓行","distance_m":200}],'
                                '"steps":[],"step_count":20,'
                                '"attribution":"高德地图 Web服务 API"}'
                            ),
                            name="plan_driving_route",
                            tool_call_id="call_driving",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    card = events[1].card
    assert card is not None
    assert card.type == "route"
    assert card.duration_basis == "traffic_aware_estimate"
    assert card.tolls_yuan == 0
    assert card.taxi_cost_yuan == 38
    assert card.traffic_lights == 4
    assert card.traffic_status_counts == {"畅通": 2, "缓行": 1}


def test_transit_tool_result_becomes_safe_structured_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=(
                                '{"mode":"transit","options":['
                                '{"distance_m":15103.0,'
                                '"duration_s":2820.0,'
                                '"walking_distance_m":839.0,'
                                '"cost_yuan":null,'
                                '"transfer_count":0,'
                                '"legs":['
                                '{"mode":"walking","distance_m":100.0,'
                                '"duration_s":120.0,"instruction":"步行至站点",'
                                '"line_name":null},'
                                '{"mode":"bus","distance_m":15003.0,'
                                '"duration_s":2700.0,"instruction":null,'
                                '"line_name":"深大校巴",'
                                '"departure_stop":"粤海校区",'
                                '"arrival_stop":"丽湖校区",'
                                '"via_stop_count":3}'
                                ']},{"distance_m":16000.0,'
                                '"duration_s":3000.0,'
                                '"walking_distance_m":500.0,'
                                '"cost_yuan":3.0,'
                                '"transfer_count":1,'
                                '"legs":['
                                '{"mode":"subway","distance_m":15500.0,'
                                '"duration_s":2500.0,"instruction":null,'
                                '"line_name":"地铁5号线",'
                                '"departure_stop":"粤海门",'
                                '"arrival_stop":"塘朗",'
                                '"via_stop_count":4}'
                                ']}],'
                                '"attribution":"高德地图 Web服务 API"}'
                            ),
                            name="plan_transit_route",
                            tool_call_id="call_transit",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == [
        "tool_completed",
        "result_card",
    ]
    card = events[1].card
    assert card is not None
    assert card.type == "transit"
    assert card.option_count == 2
    assert card.duration_s == 2820.0
    assert card.walking_distance_m == 839.0
    assert card.cost_yuan is None
    assert card.transfer_count == 0
    assert card.line_names == ["深大校巴"]
    assert len(card.options) == 2
    assert card.options[0].selected is True
    assert card.options[0].legs[0].instruction == "步行至站点"
    assert card.options[0].legs[1].departure_stop == "粤海校区"
    assert card.options[1].selected is False
    assert card.options[1].line_names == ["地铁5号线"]


def test_place_search_result_becomes_safe_structured_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=(
                                '{"query":"深圳大学","places":['
                                '{"display_name":"深圳大学粤海校区，深圳",'
                                '"latitude":22.5359023,'
                                '"longitude":113.9314749,'
                                '"category":"amenity",'
                                '"place_type":"university",'
                                '"importance":0.45}],'
                                '"attribution":"Data © OpenStreetMap contributors"}'
                            ),
                            name="search_places",
                            tool_call_id="call_place",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == [
        "tool_completed",
        "result_card",
    ]
    card = events[1].card
    assert card is not None
    assert card.type == "place"
    assert card.query == "深圳大学"
    assert len(card.places) == 1
    assert card.places[0].latitude == 22.5359023
    assert card.places[0].place_type == "university"


def test_route_endpoint_resolution_does_not_create_place_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content='{"origin":{},"destination":{}}',
                            name="resolve_route_endpoints",
                            tool_call_id="call_endpoints",
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == ["tool_completed"]


def test_composite_plan_becomes_structured_planning_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            name="recommend_travel_plan",
                            tool_call_id="call-plan",
                            content=(
                                '{"context":{"origin_name":"粤海校区",'
                                '"destination_name":"丽湖校区",'
                                '"route_snapshot_at":'
                                '"2026-09-14T08:00:00+08:00",'
                                '"arrival_deadline":{'
                                '"arrival_by":"2026-09-14T09:00:00+08:00",'
                                '"buffer_minutes":15},'
                                '"preferences":{"priority":"balanced",'
                                '"can_drive":null,"max_walking_distance_m":null,'
                                '"max_transfer_count":null}},"recommendation":{'
                                '"recommended_mode":"driving","ranked_options":['
                                '{"option":{"mode":"driving",'
                                '"duration_s":1800,'
                                '"latest_departure_at":'
                                '"2026-09-14T08:15:00+08:00"},'
                                '"scores":{"total":82}},'
                                '{"option":{"mode":"transit",'
                                '"duration_s":2700,"walking_distance_m":800,'
                                '"transfer_count":1},"scores":{"total":77}}],'
                                '"unavailable_options":[]},'
                                '"recommendation_variants":['
                                '{"priority":"balanced",'
                                '"recommended_mode":"driving","ranked_options":['
                                '{"option":{"mode":"driving",'
                                '"duration_s":1800,"cost_yuan":20},'
                                '"scores":{"total":82}}]},'
                                '{"priority":"cheapest",'
                                '"recommended_mode":"transit","ranked_options":['
                                '{"option":{"mode":"transit",'
                                '"duration_s":2700,"cost_yuan":5},'
                                '"scores":{"total":91}}]}]}'
                            ),
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == ["tool_completed", "result_card"]
    card = events[1].card
    assert card is not None
    assert card.type == "planning"
    assert card.recommended_mode == "driving"
    assert card.previous_recommended_mode is None
    assert card.reused_previous_data is False
    assert card.route_refreshed is False
    assert card.used_stale_snapshot is False
    assert card.refresh_failed is False
    assert card.route_snapshot_at is not None
    assert card.route_snapshot_at.isoformat() == "2026-09-14T08:00:00+08:00"
    assert card.arrival_by is not None
    assert card.arrival_by.isoformat() == "2026-09-14T09:00:00+08:00"
    assert card.arrival_buffer_minutes == 15
    assert card.ranked_options[0].latest_departure_at is not None
    assert (
        card.ranked_options[0].latest_departure_at.isoformat()
        == "2026-09-14T08:15:00+08:00"
    )
    assert [option.mode for option in card.ranked_options] == ["driving", "transit"]
    assert [variant.priority for variant in card.recommendation_variants] == [
        "balanced",
        "cheapest",
    ]
    assert card.recommendation_variants[1].recommended_mode == "transit"
    assert card.recommendation_variants[1].ranked_options[0].cost_yuan == 5


def test_stale_refresh_fallback_is_exposed_on_planning_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "PreferenceReplanningMiddleware.before_model": {
                    "messages": [
                        AIMessage(
                            content="使用过期快照降级",
                            additional_kwargs={
                                "travel_planning_payload": build_payload(),
                                "travel_planning_update": {
                                    "reused_previous_data": True,
                                    "used_stale_snapshot": True,
                                    "refresh_failed": True,
                                },
                            },
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    card = events[0].card
    assert card is not None
    assert card.type == "planning"
    assert card.reused_previous_data is True
    assert card.used_stale_snapshot is True
    assert card.refresh_failed is True
    assert card.route_snapshot_at is not None
    assert card.route_snapshot_at.isoformat() == "2026-09-14T10:00:00+08:00"


def test_planning_card_exposes_concrete_transit_candidates() -> None:
    payload = build_payload()
    payload["transit_candidates"] = [
        {
            "distance_m": 8_000,
            "duration_s": 3_000,
            "walking_distance_m": 300,
            "cost_yuan": 4,
            "transfer_count": 0,
            "legs": [
                {
                    "mode": "walking",
                    "distance_m": 300,
                    "duration_s": 240,
                    "instruction": "步行至地铁站",
                },
                {
                    "mode": "subway",
                    "distance_m": 7_700,
                    "duration_s": 1_500,
                    "line_name": "地铁5号线",
                    "departure_stop": "大学城站",
                    "arrival_stop": "西丽站",
                    "via_stop_count": 2,
                },
            ],
        },
        {
            "distance_m": 9_000,
            "duration_s": 1_800,
            "walking_distance_m": 900,
            "cost_yuan": 7,
            "transfer_count": 1,
            "legs": [
                {
                    "mode": "bus",
                    "distance_m": 4_000,
                    "line_name": "M176路",
                },
                {
                    "mode": "subway",
                    "distance_m": 4_100,
                    "line_name": "地铁1号线",
                },
            ],
        },
    ]
    payload["selected_transit_candidate_index"] = 1

    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            name="recommend_travel_plan",
                            tool_call_id="call-plan-candidates",
                            content=json.dumps(payload, ensure_ascii=False),
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    card = events[1].card
    assert card is not None
    assert card.type == "planning"
    assert card.selected_transit_candidate_index == 1
    assert len(card.transit_candidates) == 2
    assert card.transit_candidates[0].selected is False
    assert card.transit_candidates[0].line_names == ["地铁5号线"]
    assert len(card.transit_candidates[0].legs) == 2
    assert card.transit_candidates[0].legs[0].instruction == "步行至地铁站"
    assert card.transit_candidates[0].legs[1].departure_stop == "大学城站"
    assert card.transit_candidates[0].legs[1].via_stop_count == 2
    assert card.transit_candidates[1].selected is True
    assert card.transit_candidates[1].duration_s == 1_800
    assert card.transit_candidates[1].line_names == ["M176路", "地铁1号线"]


def test_expired_snapshot_refresh_is_exposed_on_planning_card() -> None:
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            name="recommend_travel_plan",
                            tool_call_id="refresh-plan-123",
                            content=json.dumps(build_payload(), ensure_ascii=False),
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    card = events[1].card
    assert card is not None
    assert card.type == "planning"
    assert card.route_refreshed is True
    assert card.route_snapshot_at is not None


def test_local_replan_ai_message_becomes_changed_planning_card() -> None:
    payload = {
        "context": {
            "origin_name": "粤海校区",
            "destination_name": "丽湖校区",
            "preferences": {
                "priority": "cheapest",
                "can_drive": False,
                "max_walking_distance_m": 1000,
                "max_transfer_count": 1,
            },
        },
        "recommendation": {
            "recommended_mode": "transit",
            "ranked_options": [
                {
                    "option": {
                        "mode": "transit",
                        "duration_s": 2700,
                        "walking_distance_m": 800,
                        "transfer_count": 1,
                    },
                    "scores": {"total": 84},
                }
            ],
            "unavailable_options": [
                {
                    "mode": "driving",
                    "failure_reason": "用户明确表示不能驾车",
                }
            ],
        },
        "recommendation_variants": [
            {
                "priority": "cheapest",
                "recommended_mode": "transit",
                "ranked_options": [
                    {
                        "option": {
                            "mode": "transit",
                            "duration_s": 2700,
                            "cost_yuan": 5,
                            "walking_distance_m": 800,
                            "transfer_count": 1,
                        },
                        "scores": {"total": 84},
                    }
                ],
            }
        ],
    }
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "PreferenceReplanningMiddleware.before_model": {
                    "messages": [
                        AIMessage(
                            content="已重新规划",
                            additional_kwargs={
                                "travel_planning_payload": payload,
                                "travel_planning_update": {
                                    "reused_previous_data": True,
                                    "previous_recommended_mode": "driving",
                                },
                            },
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == ["result_card"]
    card = events[0].card
    assert card is not None
    assert card.type == "planning"
    assert card.previous_recommended_mode == "driving"
    assert card.recommended_mode == "transit"
    assert card.reused_previous_data is True
    assert card.preferences.can_drive is False
    assert card.recommendation_variants[0].priority == "cheapest"
    assert card.unavailable_options[0].reason == "用户明确表示不能驾车"


def test_direct_transit_selection_ai_message_becomes_updated_card() -> None:
    payload = {
        "mode": "transit",
        "origin": {"latitude": 22.53, "longitude": 113.93},
        "destination": {"latitude": 22.59, "longitude": 113.99},
        "origin_city_code": "0755",
        "destination_city_code": "0755",
        "strategy": 7,
        "selected_transit_candidate_index": 1,
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
                        "line_name": "地铁7号线",
                    }
                ],
            },
        ],
        "attribution": "公交路线数据来源：高德地图 Web服务 API",
    }
    events = normalize_stream_part(
        {
            "type": "updates",
            "data": {
                "PreferenceReplanningMiddleware.before_model": {
                    "messages": [
                        AIMessage(
                            content="已选择公共交通候选 2",
                            additional_kwargs={"transit_route_payload": payload},
                        )
                    ]
                }
            },
        },
        THREAD_ID,
    )

    assert [event.type for event in events] == ["result_card"]
    card = events[0].card
    assert card is not None
    assert card.type == "transit"
    assert card.duration_s == 4_200
    assert card.line_names == ["地铁7号线"]
    assert card.options[0].selected is False
    assert card.options[1].selected is True
