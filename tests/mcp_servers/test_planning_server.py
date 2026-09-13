import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from mcp.types import CallToolResult, TextContent

from travel_agent.domain.decision import (
    JourneyContext,
    ScoreBreakdown,
    ScoredTravelOption,
    TravelComparisonResult,
    TravelOption,
    TravelPreferences,
    TravelRecommendation,
)
from travel_agent.mcp_servers import planning_server


def _parse_text_result(result: CallToolResult) -> dict[str, object]:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return json.loads(content.text)


class FakePlanningService:
    received: dict[str, object] = {}

    async def compare(self, **kwargs: object) -> TravelComparisonResult:
        self.received = kwargs
        option = TravelOption(
            mode="transit",
            distance_m=8000,
            duration_s=2400,
            walking_distance_m=500,
            cost_yuan=5,
            transfer_count=1,
            attribution="amap transit",
        )
        return TravelComparisonResult(
            context=JourneyContext(
                city=str(kwargs["city"]),
                origin_name=str(kwargs["origin_name"]),
                destination_name=str(kwargs["destination_name"]),
                departure_time=kwargs.get("departure_time"),
                preferences=kwargs["preferences"],
            ),
            recommendation=TravelRecommendation(
                recommended_mode="transit",
                ranked_options=[
                    ScoredTravelOption(
                        option=option,
                        scores=ScoreBreakdown(
                            time=80,
                            cost=100,
                            walking=70,
                            transfers=80,
                            weather_fit=90,
                            total=84,
                        ),
                        reasons=["综合得分最高"],
                    )
                ],
                confidence=0.4,
                summary_reasons=["公交综合得分最高"],
            ),
        )


@pytest.mark.asyncio
async def test_recommend_travel_plan_builds_preferences_and_returns_json(
    monkeypatch,
) -> None:
    fake = FakePlanningService()
    monkeypatch.setattr(planning_server, "TravelPlanningService", lambda: fake)
    monkeypatch.setattr(
        planning_server,
        "get_settings",
        lambda: SimpleNamespace(business_timezone="Asia/Shanghai"),
    )

    result = await planning_server.recommend_travel_plan(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin_latitude=22.5359,
        origin_longitude=113.9315,
        destination_latitude=22.6009,
        destination_longitude=113.9879,
        priority="least_walking",
        can_drive=False,
        max_walking_distance_m=1000,
        max_transfer_count=2,
    )

    payload = _parse_text_result(result)
    assert payload["recommendation"]["recommended_mode"] == "transit"
    preferences = fake.received["preferences"]
    assert isinstance(preferences, TravelPreferences)
    assert preferences.priority == "least_walking"
    assert preferences.can_drive is False
    assert preferences.max_walking_distance_m == 1000
    assert result.meta == {
        "observability": {
            "external_http_request_count": 0,
            "external_http_requests": [],
        }
    }


@pytest.mark.asyncio
async def test_recommend_travel_plan_parses_future_departure_text(
    monkeypatch,
) -> None:
    fake = FakePlanningService()
    reference_at = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    monkeypatch.setattr(planning_server, "TravelPlanningService", lambda: fake)
    monkeypatch.setattr(
        planning_server,
        "get_settings",
        lambda: SimpleNamespace(business_timezone="Asia/Shanghai"),
    )
    monkeypatch.setattr(planning_server, "_now", lambda _timezone: reference_at)

    result = await planning_server.recommend_travel_plan(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin_latitude=22.5359,
        origin_longitude=113.9315,
        destination_latitude=22.6009,
        destination_longitude=113.9879,
        departure_time_text="明天下午三点",
    )

    payload = _parse_text_result(result)
    assert payload["context"]["departure_time"]["precision"] == "exact"
    departure = fake.received["departure_time"]
    assert departure is not None
    assert departure.departure_at.hour == 15
    assert fake.received["reference_at"] == reference_at


@pytest.mark.asyncio
async def test_recommend_travel_plan_rejects_date_without_time_period(
    monkeypatch,
) -> None:
    reference_at = datetime(
        2026,
        9,
        12,
        10,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    monkeypatch.setattr(
        planning_server,
        "get_settings",
        lambda: SimpleNamespace(business_timezone="Asia/Shanghai"),
    )
    monkeypatch.setattr(planning_server, "_now", lambda _timezone: reference_at)

    result = await planning_server.recommend_travel_plan(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin_latitude=22.5359,
        origin_longitude=113.9315,
        destination_latitude=22.6009,
        destination_longitude=113.9879,
        departure_time_text="明天出发",
    )

    payload = _parse_text_result(result)
    assert payload["ok"] is False
    assert "补充上午、下午、晚上或具体时间" in payload["error"]


@pytest.mark.asyncio
async def test_recommend_travel_plan_returns_safe_validation_error() -> None:
    result = await planning_server.recommend_travel_plan(
        city="深圳",
        origin_name="起点",
        destination_name="终点",
        origin_latitude=91,
        origin_longitude=113.9,
        destination_latitude=22.6,
        destination_longitude=114.0,
    )

    payload = _parse_text_result(result)
    assert payload["ok"] is False
    assert "参数无效" in payload["error"]
    assert "pydantic.dev" not in payload["error"]
