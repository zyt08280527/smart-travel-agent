from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.services.test_single_departure_watch import NOW, saved_trip
from travel_agent.api.app import create_app
from travel_agent.services.departure_watch import DepartureWatch
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


@pytest.mark.asyncio
async def test_notification_read_detail_restart_and_stale_token(tmp_path):
    storage, saved = await saved_trip(tmp_path)
    workspace = MultiStopWorkspace(tmp_path / "notices.sqlite")
    checker = AsyncMock()
    service = DepartureWatch(workspace, AsyncMock(), AsyncMock(), checker)
    task = await service.enable_single(saved, NOW + timedelta(hours=1), now=NOW)
    check_at = NOW + timedelta(minutes=40)
    service._claim(check_at)
    service._finish(task["id"], "pending", "数据未知，稍后重试", check_at)

    def app():
        return create_app(
            runtime_factory=FakeRuntime,
            multi_stop_workspace_factory=lambda: workspace,
            itinerary_service_factory=lambda: storage,
            single_watch_checker_factory=lambda: checker,
        )

    endpoint = f"/api/departure-watches/{task['id']}"
    with TestClient(app()) as client:
        item = client.get("/api/departure-watches").json()[0]
        assert item["unread"] and item["title"] == "起点 → 终点"
        detail = client.get(endpoint).json()
        assert detail["snapshot"]["itinerary_id"] == saved.itinerary_id
        assert detail["kind"] == "single" and "不要保存" in detail["replan_prompt"]
        read = {"notification_token": item["notification_token"]}
        assert client.post(endpoint + "/read", json=read).status_code == 204
        assert client.post(endpoint + "/read", json=read).status_code == 204
    with TestClient(app()) as client:
        assert not client.get("/api/departure-watches").json()[0]["unread"]
        later = check_at + timedelta(minutes=5)
        service._claim(later)
        service._finish(task["id"], "completed", "检查完成，有降水信号", later)
        assert client.post(endpoint + "/read", json=read).status_code == 409
        assert client.get("/api/departure-watches").json()[0]["unread"]
        assert client.get(f"/api/departure-watches/{uuid4()}").status_code == 404
        assert (
            client.post(endpoint + "/read", json={"notification_token": "fake"}).status_code == 422
        )
        assert client.delete(endpoint).status_code == 204
        assert client.get("/api/departure-watches").json()[0]["status"] == "cancelled"
    assert await storage.get(saved.itinerary_id) == saved
    checker.check.assert_not_awaited()
