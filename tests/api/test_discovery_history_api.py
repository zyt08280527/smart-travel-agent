from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.services.test_discovery import discovery_request, planned
from travel_agent.api.app import create_app
from travel_agent.services.conversation import ConversationRepository
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def test_history_reopen_refine_choose_and_selected_plan_recovery_after_restart(tmp_path):
    repository = ConversationRepository(tmp_path / "history.sqlite")
    planner = AsyncMock()
    planner.plan.side_effect = planned
    workspace = MultiStopWorkspace(tmp_path / "drafts.sqlite")

    class HistoryRuntime(FakeRuntime):
        async def get_conversation(self, thread_id):
            return await repository.get_conversation(thread_id)

        async def list_conversations(self, limit=50):
            return await repository.list_conversations(limit)

    def app():
        return create_app(runtime_factory=HistoryRuntime,
                          conversation_repository_factory=lambda: repository,
                          multi_stop_workspace_factory=lambda: workspace,
                          multi_stop_service_factory=lambda: planner)

    thread = str(uuid4())
    criteria = discovery_request(departure_at=None).model_dump(mode="json")
    with TestClient(app()) as client:
        first = client.post("/api/discovery/recommend", json={**criteria, "thread_id": thread})
        assert first.status_code == 200
        assert first.json()["history"]["revision"] == 1
        assert client.get("/api/conversations").json()[0]["thread_id"] == thread
        clarification = client.post("/api/discovery/refine", json={
            "thread_id": thread, "expected_revision": 1, "request": criteria,
            "message": "只有两小时",
        }).json()
        assert clarification["history"]["revision"] == 2
    # Recreate the API instance and load only the server record, without a browser cache.
    with TestClient(app()) as client:
        detail = client.get(f"/api/conversations/{thread}").json()
        assert len(detail["messages"]) == 4
        assert detail["discovery"]["pending_changes"] == {"available_minutes": 120}
        assert detail["discovery"]["revision"] == 2
        assert client.post("/api/discovery/refine", json={
            "thread_id": thread, "expected_revision": 1, "request": criteria,
            "message": "预算改成1元",
        }).status_code == 409
        completed = client.post("/api/discovery/refine", json={
            "thread_id": thread, "expected_revision": 2, "request": criteria,
            "message": "停留改成60分钟",
        }).json()
        assert completed["request"]["available_minutes"] == 120
        assert completed["request"]["stay_minutes"] == 60
        candidate = completed["result"]["candidates"][0]["card"]["attraction_id"]
        chosen = client.post(f"/api/conversations/{thread}/discovery/choose", json={
            "expected_revision": 3, "attraction_id": candidate,
        }).json()
        draft = chosen["draft"]
        plan = draft["result"]["selection"]["plans"][-1]["plan_id"]
        changed = client.post(f"/api/conversations/{thread}/discovery/selection", json={
            "expected_revision": 4, "draft_id": draft["draft_id"], "plan_id": plan,
        })
        assert changed.status_code == 200
        restored = client.get(f"/api/conversations/{thread}").json()
        assert restored["discovery"]["draft_id"] == draft["draft_id"]
        assert restored["discovery"]["plan_id"] == plan
        assert len(restored["messages"]) == 8
        assert restored["messages"][-1]["cards"][0]["draft_id"] == draft["draft_id"]
        assert client.get("/api/multi-stop/itineraries").json() == []
