import json
from datetime import UTC, datetime

import pytest

from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.mcp_servers import itinerary_server


@pytest.mark.asyncio
async def test_save_itinerary_returns_user_safe_validation_error() -> None:
    result = await itinerary_server.save_itinerary(
        title="",
        origin="起点",
        destination="终点",
        travel_mode="driving",
        distance_m=-1,
        duration_s=10,
        duration_basis="static_without_live_traffic",
    )

    payload = json.loads(result)
    assert payload["ok"] is False
    assert "行程参数无效" in payload["error"]
    assert "pydantic.dev" not in payload["error"]


@pytest.mark.asyncio
async def test_save_itinerary_returns_saved_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeItineraryService:
        async def save(self, draft: object) -> SavedItinerary:
            return SavedItinerary(
                title="测试行程",
                origin="起点",
                destination="终点",
                travel_mode="driving",
                distance_m=1000,
                duration_s=300,
                notes=None,
                itinerary_id="test-id",
                saved_at=datetime(2026, 7, 23, tzinfo=UTC),
            )

    monkeypatch.setattr(
        itinerary_server,
        "ItineraryService",
        FakeItineraryService,
    )

    result = await itinerary_server.save_itinerary(
        title="测试行程",
        origin="起点",
        destination="终点",
        travel_mode="driving",
        distance_m=1000,
        duration_s=300,
        duration_basis="static_without_live_traffic",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["itinerary"]["itinerary_id"] == "test-id"
    assert (
        payload["itinerary"]["duration_basis"]
        == "static_without_live_traffic"
    )


@pytest.mark.asyncio
async def test_save_itinerary_accepts_walking_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeItineraryService:
        async def save(self, draft: object) -> SavedItinerary:
            return SavedItinerary(
                title="深大两校区步行计划",
                origin="深圳大学粤海校区",
                destination="深圳大学丽湖校区",
                travel_mode="walking",
                distance_m=13709.7,
                duration_s=9870.9,
                notes=None,
                itinerary_id="walking-test-id",
                saved_at=datetime(2026, 7, 24, tzinfo=UTC),
            )

    monkeypatch.setattr(
        itinerary_server,
        "ItineraryService",
        FakeItineraryService,
    )

    result = await itinerary_server.save_itinerary(
        title="深大两校区步行计划",
        origin="深圳大学粤海校区",
        destination="深圳大学丽湖校区",
        travel_mode="walking",
        distance_m=13709.7,
        duration_s=9870.9,
        duration_basis="static_without_live_traffic",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["itinerary"]["travel_mode"] == "walking"


@pytest.mark.asyncio
async def test_save_itinerary_accepts_transit_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeItineraryService:
        async def save(self, draft: object) -> SavedItinerary:
            return SavedItinerary(
                title="深大两校区公共交通计划",
                origin="深圳大学粤海校区",
                destination="深圳大学丽湖校区",
                travel_mode="transit",
                distance_m=15103,
                duration_s=2820,
                notes="步行839米，乘坐深大校巴通勤车",
                itinerary_id="transit-test-id",
                saved_at=datetime(2026, 7, 24, tzinfo=UTC),
            )

    monkeypatch.setattr(
        itinerary_server,
        "ItineraryService",
        FakeItineraryService,
    )

    result = await itinerary_server.save_itinerary(
        title="深大两校区公共交通计划",
        origin="深圳大学粤海校区",
        destination="深圳大学丽湖校区",
        travel_mode="transit",
        distance_m=15103,
        duration_s=2820,
        duration_basis="static_without_live_traffic",
        notes="步行839米，乘坐深大校巴通勤车",
    )

    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["itinerary"]["travel_mode"] == "transit"
