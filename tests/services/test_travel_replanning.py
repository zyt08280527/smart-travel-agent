from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from travel_agent.domain.decision import JourneyContext
from travel_agent.services.travel_replanning import (
    build_route_refresh_args,
    is_route_snapshot_fresh,
    replan_from_payload,
    replan_from_stale_payload,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
SNAPSHOT_AT = datetime(2026, 9, 14, 10, 0, tzinfo=SHANGHAI)


def build_snapshot_context() -> JourneyContext:
    return JourneyContext(
        city="深圳市",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        route_snapshot_at=SNAPSHOT_AT,
        route_snapshot_expires_at=SNAPSHOT_AT + timedelta(minutes=5),
    )


def test_route_snapshot_is_fresh_inside_ttl() -> None:
    assert is_route_snapshot_fresh(
        build_snapshot_context(),
        reference_at=SNAPSHOT_AT + timedelta(minutes=4, seconds=59),
    )


def test_route_snapshot_expires_at_ttl_boundary() -> None:
    assert not is_route_snapshot_fresh(
        build_snapshot_context(),
        reference_at=SNAPSHOT_AT + timedelta(minutes=5),
    )


def test_legacy_context_without_snapshot_metadata_is_stale() -> None:
    context = JourneyContext(
        city="深圳市",
        origin_name="粤海校区",
        destination_name="丽湖校区",
    )

    assert not is_route_snapshot_fresh(context, reference_at=SNAPSHOT_AT)


def test_snapshot_freshness_requires_timezone_aware_reference() -> None:
    with pytest.raises(ValueError, match="reference_at must include timezone"):
        is_route_snapshot_fresh(
            build_snapshot_context(),
            reference_at=datetime(2026, 9, 14, 10, 1),
        )


def build_payload() -> dict[str, object]:
    return {
        "context": {
            "city": "深圳市",
            "origin_name": "粤海校区",
            "destination_name": "丽湖校区",
            "origin": {"latitude": 22.533, "longitude": 113.93},
            "destination": {"latitude": 22.586, "longitude": 113.97},
            "route_snapshot_at": SNAPSHOT_AT.isoformat(),
            "route_snapshot_expires_at": (
                SNAPSHOT_AT + timedelta(minutes=5)
            ).isoformat(),
            "preferences": {"priority": "balanced"},
            "weather": {
                "location": {
                    "name": "深圳",
                    "country": "中国",
                    "admin1": "广东",
                    "latitude": 22.54,
                    "longitude": 114.06,
                },
                "temperature_c": 28,
                "apparent_temperature_c": 30,
                "precipitation_mm": 0,
                "wind_speed_kmh": 8,
                "weather_code": 1,
                "condition": "大部晴朗",
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
                        "distance_m": 15000,
                        "duration_s": 1800,
                        "duration_basis": "traffic_aware_estimate",
                        "transfer_count": 0,
                        "attribution": "高德驾车",
                    },
                    "scores": {
                        "time": 100,
                        "cost": 50,
                        "walking": 100,
                        "transfers": 100,
                        "weather_fit": 95,
                        "total": 82,
                    },
                },
                {
                    "option": {
                        "mode": "transit",
                        "distance_m": 15000,
                        "duration_s": 2700,
                        "walking_distance_m": 800,
                        "cost_yuan": 5,
                        "transfer_count": 1,
                        "attribution": "高德公交",
                    },
                    "scores": {
                        "time": 70,
                        "cost": 80,
                        "walking": 80,
                        "transfers": 50,
                        "weather_fit": 90,
                        "total": 77,
                    },
                },
                {
                    "option": {
                        "mode": "walking",
                        "distance_m": 12500,
                        "duration_s": 9000,
                        "walking_distance_m": 12500,
                        "cost_yuan": 0,
                        "transfer_count": 0,
                        "attribution": "高德步行",
                    },
                    "scores": {
                        "time": 0,
                        "cost": 100,
                        "walking": 0,
                        "transfers": 100,
                        "weather_fit": 100,
                        "total": 38,
                    },
                },
            ],
            "unavailable_options": [],
            "summary_reasons": ["驾车综合得分最高"],
            "limitations": ["天气使用出发时段预报；路线数据仍为查询时结果"],
        },
    }


def test_build_route_refresh_args_reuses_facts_and_merges_preferences() -> None:
    context_value = build_payload()["context"]
    assert isinstance(context_value, dict)
    context = JourneyContext.model_validate(context_value)

    args = build_route_refresh_args(
        context,
        preference_updates={"can_drive": False, "priority": "cheapest"},
    )

    assert args["city"] == "深圳市"
    assert args["origin_latitude"] == 22.533
    assert args["destination_longitude"] == 113.97
    assert args["can_drive"] is False
    assert args["priority"] == "cheapest"


def test_stale_fallback_is_explicit_and_applies_new_preferences() -> None:
    result = replan_from_stale_payload(
        build_payload(),
        preference_updates={"can_drive": False},
    )

    assert result["refresh_fallback"] == {
        "used_stale_snapshot": True,
        "reason": "route_refresh_failed",
    }
    context = result["context"]
    recommendation = result["recommendation"]
    assert context["preferences"]["can_drive"] is False
    assert recommendation["recommended_mode"] == "transit"
    assert recommendation["limitations"][0] == (
        "路线自动刷新失败，以下结果基于已过期快照，仅供临时参考"
    )


def test_replan_excludes_driving_and_reuses_existing_facts() -> None:
    result = replan_from_payload(
        build_payload(),
        preference_updates={"can_drive": False},
    )

    context = result["context"]
    recommendation = result["recommendation"]
    assert context["origin_name"] == "粤海校区"
    assert context["weather"]["condition"] == "大部晴朗"
    assert context["preferences"]["can_drive"] is False
    assert recommendation["recommended_mode"] == "transit"
    assert recommendation["unavailable_options"][0]["mode"] == "driving"
    assert "不能驾车" in recommendation["unavailable_options"][0]["failure_reason"]
    assert any("路线数据仍为查询时结果" in item for item in recommendation["limitations"])
    variants = result["recommendation_variants"]
    assert len(variants) == 5
    assert all(
        scored["option"]["mode"] != "driving"
        for variant in variants
        for scored in variant["ranked_options"]
    )


def test_replan_preserves_explicit_mode_selection_across_priority_changes() -> None:
    payload = build_payload()
    payload["selected_mode"] = "transit"

    result = replan_from_payload(
        payload,
        preference_updates={"priority": "fastest"},
    )

    assert result["selected_mode"] == "transit"


def test_replan_rejects_explicitly_selected_unavailable_mode() -> None:
    with pytest.raises(ValueError, match="所选交通方式当前不可用"):
        replan_from_payload(
            build_payload(),
            preference_updates={"can_drive": False},
            selected_mode="driving",
        )


def test_replan_reselects_concrete_transit_candidate_for_new_priority() -> None:
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

    result = replan_from_payload(
        payload,
        preference_updates={"priority": "least_walking"},
    )

    assert result["selected_transit_candidate_index"] == 0
    transit = next(
        scored["option"]
        for scored in result["recommendation"]["ranked_options"]
        if scored["option"]["mode"] == "transit"
    )
    assert transit["duration_s"] == 3_000
    assert transit["walking_distance_m"] == 300
    assert result["context"]["preferences"]["priority"] == "least_walking"


def test_replan_uses_explicit_public_transit_candidate_without_provider_call() -> None:
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

    result = replan_from_payload(
        payload,
        preference_updates={},
        transit_candidate_index=0,
    )

    assert result["selected_transit_candidate_index"] == 0
    transit = next(
        scored["option"]
        for scored in result["recommendation"]["ranked_options"]
        if scored["option"]["mode"] == "transit"
    )
    assert transit["duration_s"] == 3_000
    assert transit["walking_distance_m"] == 300


def test_replan_rejects_unknown_public_transit_candidate() -> None:
    payload = build_payload()
    payload["transit_candidates"] = [
        {
            "distance_m": 8_000,
            "duration_s": 3_000,
            "walking_distance_m": 300,
            "cost_yuan": 4,
            "transfer_count": 0,
            "legs": [{"mode": "walking", "distance_m": 300}],
        }
    ]
    payload["selected_transit_candidate_index"] = 0

    with pytest.raises(ValueError, match="候选不存在"):
        replan_from_payload(
            payload,
            preference_updates={},
            transit_candidate_index=2,
        )


def test_replan_does_not_restore_an_expired_arrival_option() -> None:
    payload = build_payload()
    context = payload["context"]
    assert isinstance(context, dict)
    context["arrival_deadline"] = {
        "arrival_by": "2026-09-14T09:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "precision": "exact",
        "source_text": "明天9点前",
        "buffer_minutes": 15,
    }
    recommendation = payload["recommendation"]
    assert isinstance(recommendation, dict)
    unavailable = recommendation["unavailable_options"]
    assert isinstance(unavailable, list)
    unavailable.append(
        {
            "mode": "transit",
            "status": "unavailable",
            "failure_reason": "按照预计耗时和 15 分钟缓冲，最晚出发时间已过",
        }
    )
    ranked = recommendation["ranked_options"]
    assert isinstance(ranked, list)
    ranked[:] = [item for item in ranked if item["option"]["mode"] != "transit"]

    result = replan_from_payload(
        payload,
        preference_updates={"priority": "cheapest"},
    )

    replanned = result["recommendation"]
    assert all(
        scored["option"]["mode"] != "transit"
        for scored in replanned["ranked_options"]
    )
    transit = next(
        option
        for option in replanned["unavailable_options"]
        if option["mode"] == "transit"
    )
    assert "最晚出发时间已过" in transit["failure_reason"]
