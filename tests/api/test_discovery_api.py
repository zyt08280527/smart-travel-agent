from datetime import UTC, datetime
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.services.test_discovery import discovery_request, planned
from tests.services.test_discovery_weather import report_for
from travel_agent.api.app import create_app


def test_discovery_api_filters_and_does_not_save_or_need_model(tmp_path):
    from travel_agent.services.multi_stop_workspace import MultiStopWorkspace

    planner = AsyncMock()
    planner.plan.side_effect = planned
    app = create_app(
        runtime_factory=FakeRuntime, multi_stop_service_factory=lambda: planner,
        multi_stop_workspace_factory=lambda: MultiStopWorkspace(tmp_path / "discovery.sqlite"),
    )
    # Far future: no valid ticket facts, so strict budget never queries routes.
    data = discovery_request(budget_yuan=10).model_dump(mode="json")
    data["departure_at"] = "2030-01-01T09:00:00+08:00"
    with TestClient(app) as client:
        response = client.post("/api/discovery/recommend", json=data)
        assert response.status_code == 200
        assert response.json()["candidates"] == []
        planner.plan.assert_not_awaited()
        data["budget_yuan"] = None
        response = client.post("/api/discovery/recommend", json=data)
        assert response.status_code == 200
        assert len(response.json()["candidates"]) == 3
        assert not (tmp_path / "discovery.sqlite").exists()
        data["departure_at"] = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
        assert client.post("/api/discovery/recommend", json=data).status_code == 422


def test_discovery_api_weather_policy_uses_injected_service_and_rejects_unknown_policy(tmp_path):
    from travel_agent.services.multi_stop_workspace import MultiStopWorkspace

    planner, weather = AsyncMock(), AsyncMock()
    planner.plan.side_effect = planned
    weather.report.side_effect = lambda draft, plan_id, now: report_for(
        draft, plan_id, now=now, status="unavailable",
    )
    app = create_app(
        runtime_factory=FakeRuntime, multi_stop_service_factory=lambda: planner,
        multi_stop_weather_factory=lambda: weather,
        multi_stop_workspace_factory=lambda: MultiStopWorkspace(tmp_path / "no-save.sqlite"),
    )
    data = discovery_request(weather_policy="inform").model_dump(mode="json")
    data["departure_at"] = "2030-01-01T09:00:00+08:00"
    with TestClient(app) as client:
        result = client.post("/api/discovery/recommend", json=data)
        assert result.status_code == 200
        assert len(result.json()["candidates"]) == 3
        assert result.json()["candidates"][0]["weather"]["stops"][0]["status"] == "unavailable"
        data["weather_policy"] = "exclude_signals"
        result = client.post("/api/discovery/recommend", json=data)
        assert result.json()["candidates"] == []
        data["weather_policy"] = "guess-clear"
        assert client.post("/api/discovery/recommend", json=data).status_code == 422
    assert weather.report.await_count == 6
    assert not (tmp_path / "no-save.sqlite").exists()
