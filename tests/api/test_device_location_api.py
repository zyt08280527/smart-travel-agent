from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.services.test_discovery import planned
from travel_agent.api.app import create_app
from travel_agent.domain.place import Place
from travel_agent.services.amap_city import AmapCityServiceError
from travel_agent.services.conversation import ConversationRepository
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def test_position_to_recommendation_history_and_chosen_roundtrip_keep_same_origin(tmp_path):
    captured = datetime.now(UTC).isoformat()
    place = Place(
        display_name="当前位置：深圳市南山区某路",
        latitude=22.53,
        longitude=113.93,
        city_code="440300",
        location_source="device",
        location_accuracy_m=25,
        location_captured_at=captured,
    )
    locator, planner = AsyncMock(), AsyncMock()
    locator.resolve.return_value = place
    planner.plan.side_effect = planned
    app = create_app(
        runtime_factory=FakeRuntime,
        device_location_factory=lambda: locator,
        multi_stop_service_factory=lambda: planner,
        multi_stop_workspace_factory=lambda: MultiStopWorkspace(tmp_path / "multi.sqlite"),
        conversation_repository_factory=lambda: ConversationRepository(tmp_path / "chat.sqlite"),
    )
    body = dict(latitude=22.53, longitude=113.93, accuracy_m=25, captured_at=captured)
    thread = str(uuid4())
    with TestClient(app) as client:
        response = client.post("/api/places/device-location", json=body)
        assert response.status_code == 200
        origin = response.json()
        assert origin["provider_place_id"] is None
        recommendation = client.post(
            "/api/discovery/recommend",
            json={
                "origin": origin,
                "available_minutes": 240,
                "stay_minutes": 60,
                "thread_id": thread,
                "expected_revision": 0,
            },
        )
        assert recommendation.status_code == 200
        result = recommendation.json()
        assert result["candidates"]
        chosen = client.post(
            f"/api/conversations/{thread}/discovery/choose",
            json={
                "attraction_id": result["candidates"][0]["card"]["attraction_id"],
                "expected_revision": result["history"]["revision"],
            },
        )
        assert chosen.status_code == 200
        stops = chosen.json()["draft"]["result"]["request"]["stops"]
        assert stops[0]["point"] == stops[-1]["point"] == {"latitude": 22.53, "longitude": 113.93}
        assert stops[0]["name"] == place.display_name
        assert client.get("/api/multi-stop/itineraries").json() == []
        assert (
            client.post("/api/places/device-location", json={**body, "accuracy_m": 501}).status_code
            == 422
        )
        locator.resolve.side_effect = AmapCityServiceError("provider failed")
        assert client.post("/api/places/device-location", json=body).status_code == 503
