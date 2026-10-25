from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.services.test_discovery import discovery_request, planned
from travel_agent.api.app import create_app
from travel_agent.services.attraction import load_profiles
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def test_refinement_keeps_pending_constraints_then_recomputes_and_excludes_without_saving(tmp_path):
    planner = AsyncMock()
    planner.plan.side_effect = planned
    path = tmp_path / "unused.sqlite"
    app = create_app(runtime_factory=FakeRuntime, multi_stop_service_factory=lambda: planner,
                     multi_stop_workspace_factory=lambda: MultiStopWorkspace(path))
    body = {"request": discovery_request(departure_at=None).model_dump(mode="json"),
            "candidate_ids": [profile.card.attraction_id for profile in load_profiles()],
            "message": "只有两小时"}
    with TestClient(app) as client:
        first = client.post("/api/discovery/refine", json=body)
        assert first.status_code == 200
        assert first.json()["status"] == "clarification"
        assert first.json()["request"]["available_minutes"] == 240
        assert first.json()["pending_changes"] == {"available_minutes": 120}
        planner.plan.assert_not_awaited()
        body["pending_changes"] = first.json()["pending_changes"]
        body["message"] = "停留改成60分钟"
        second = client.post("/api/discovery/refine", json=body)
        assert second.status_code == 200
        reply = second.json()
        assert reply["status"] == "updated"
        assert reply["request"]["available_minutes"] == 120
        assert len(reply["result"]["candidates"]) == 3
        for call in planner.plan.await_args_list:
            assert call.args[0].max_elapsed_minutes == 120
            assert call.args[0].stops[1].stay_minutes == 60
        planner.reset_mock()
        body.update(request=reply["request"], pending_changes=None, message="换一个目的地")
        target = body["candidate_ids"][0]
        third = client.post("/api/discovery/refine", json=body).json()
        assert third["status"] == "updated"
        assert len(third["result"]["candidates"]) == 2
        assert target not in [item["card"]["attraction_id"]
                              for item in third["result"]["candidates"]]
        assert any("用户已明确排除" in item["reason"] for item in third["result"]["excluded"])
        assert planner.plan.await_count == 2
        body["candidate_ids"] = ["invented"]
        assert client.post("/api/discovery/refine", json=body).status_code == 422
    assert not path.exists()
