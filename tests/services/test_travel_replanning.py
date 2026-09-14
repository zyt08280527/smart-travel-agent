from travel_agent.services.travel_replanning import replan_from_payload


def build_payload() -> dict[str, object]:
    return {
        "context": {
            "city": "深圳市",
            "origin_name": "粤海校区",
            "destination_name": "丽湖校区",
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
