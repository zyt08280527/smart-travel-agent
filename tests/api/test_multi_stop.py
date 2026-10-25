from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.api.test_app import FakeRuntime
from tests.services.test_multi_stop_selection import scenario
from tests.services.test_multi_stop_timeline import request_data, route_legs
from travel_agent.api.app import create_app
from travel_agent.api.streaming import result_card_from_tool_message
from travel_agent.domain.multi_stop import LegCandidates, MultiStopPlanningResult, MultiStopRequest
from travel_agent.middleware.itinerary_save_grounding import ItinerarySaveGroundingMiddleware
from travel_agent.middleware.preference_replanning import PreferenceReplanningMiddleware
from travel_agent.middleware.recommendation_explanation import latest_planning_payload
from travel_agent.services.amap_place import AmapPlaceService
from travel_agent.services.multi_stop_selection import select_multi_stop_plans
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def fixture_result():
    request, groups = scenario()
    return MultiStopPlanningResult(
        request=request,
        segments=groups,
        selection=select_multi_stop_plans(request, groups),
        route_service_calls=0,
    )


def test_plan_select_save_and_reopen_full_snapshot_after_app_restart(tmp_path):
    workspace = MultiStopWorkspace(tmp_path / "multi.sqlite")
    result = fixture_result()
    service = AsyncMock()
    service.plan.return_value = result

    def factory():
        return workspace

    app = create_app(
        runtime_factory=FakeRuntime,
        multi_stop_service_factory=lambda: service,
        multi_stop_workspace_factory=factory,
    )
    with TestClient(app) as client:
        assert client.get("/api/multi-stop/itineraries").json() == []
        response = client.post("/api/multi-stop/plan", json=result.request.model_dump(mode="json"))
        assert response.status_code == 200
        draft = response.json()
        selected = draft["result"]["selection"]["plans"][1]["plan_id"]
        assert selected != draft["result"]["selection"]["recommended_plan_id"]
        saved = client.post(
            f"/api/multi-stop/drafts/{draft['draft_id']}/save", json={"selected_plan_id": selected}
        )
        assert saved.status_code == 200
        assert saved.json()["selected_plan_id"] == selected
        repeated = client.post(
            f"/api/multi-stop/drafts/{draft['draft_id']}/save", json={"selected_plan_id": selected}
        )
        assert repeated.json()["itinerary_id"] == saved.json()["itinerary_id"]
    restarted = create_app(runtime_factory=FakeRuntime, multi_stop_workspace_factory=factory)
    with TestClient(restarted) as client:
        records = client.get("/api/multi-stop/itineraries").json()
        assert len(records) == 1
        assert records[0] == saved.json()
        assert client.get(f"/api/multi-stop/drafts/{draft['draft_id']}").json() == draft
    service.plan.assert_awaited_once()


def test_save_rejects_invented_plan_client_metrics_or_missing_draft(tmp_path):
    workspace = MultiStopWorkspace(tmp_path / "multi.sqlite")
    service = AsyncMock()
    result = fixture_result()
    service.plan.return_value = result
    with TestClient(
        create_app(
            runtime_factory=FakeRuntime,
            multi_stop_service_factory=lambda: service,
            multi_stop_workspace_factory=lambda: workspace,
        )
    ) as client:
        draft = client.post(
            "/api/multi-stop/plan", json=result.request.model_dump(mode="json")
        ).json()
        path = f"/api/multi-stop/drafts/{draft['draft_id']}/save"
        assert client.post(path, json={"selected_plan_id": "invented"}).status_code == 422
        assert (
            client.post(
                path, json={"selected_plan_id": result.selection.plans[0].plan_id, "duration_s": 1}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/multi-stop/drafts/{uuid4()}/save", json={"selected_plan_id": "fake"}
            ).status_code
            == 404
        )
        assert client.get("/api/multi-stop/itineraries").json() == []


def test_invalid_multi_request_does_not_call_providers(tmp_path):
    service = AsyncMock()
    with TestClient(
        create_app(runtime_factory=FakeRuntime, multi_stop_service_factory=lambda: service)
    ) as client:
        payload = fixture_result().request.model_dump(mode="json")
        payload["stops"][1]["city_code"] = "other-city"
        assert client.post("/api/multi-stop/plan", json=payload).status_code == 422
    service.plan.assert_not_awaited()


def test_five_stops_repeated_visit_waits_stays_and_reopen_keep_full_evidence(tmp_path):
    data = request_data()
    middle = {**data["stops"][1], "stop_id": "third-visit", "name": "第三个中间点"}
    data["stops"].insert(-1, middle)
    data["stops"][-1]["name"] = data["stops"][0]["name"]
    for stop, stay in zip(data["stops"], [0, 10, 20, 30, 0], strict=True):
        stop["stay_minutes"] = stay
    data["stops"][1]["earliest_arrival_at"] = "2026-10-24T10:00:00+08:00"
    request = MultiStopRequest.model_validate(data)
    legs = route_legs(request, minutes=[10, 20, 30, 40])
    groups = [LegCandidates(origin_stop_id=leg.origin_stop_id,
                            destination_stop_id=leg.destination_stop_id, candidates=[leg])
              for leg in legs]
    result = MultiStopPlanningResult(request=request, segments=groups,
                                    selection=select_multi_stop_plans(request, groups),
                                    route_service_calls=0)
    service = AsyncMock()
    service.plan.return_value = result
    workspace = MultiStopWorkspace(tmp_path / "five.sqlite")
    app = create_app(runtime_factory=FakeRuntime, multi_stop_service_factory=lambda: service,
                     multi_stop_workspace_factory=lambda: workspace)
    with TestClient(app) as client:
        response = client.post("/api/multi-stop/plan", json=request.model_dump(mode="json"))
        assert response.status_code == 200
        draft = response.json()
        plan = draft["result"]["selection"]["plans"][0]
        timeline = plan["evaluation"]
        assert [stop["stop_id"] for stop in timeline["stops"]] == [s.stop_id for s in request.stops]
        assert len(timeline["legs"]) == 4
        assert timeline["totals"]["waiting_s"] == 50 * 60
        assert timeline["totals"]["stay_s"] == 60 * 60
        assert timeline["totals"]["elapsed_s"] == 210 * 60
        assert timeline["stops"][-1]["arrival_at"] == "2026-10-24T12:30:00+08:00"
        saved = client.post(f"/api/multi-stop/drafts/{draft['draft_id']}/save",
                            json={"selected_plan_id": plan["plan_id"]})
        assert saved.status_code == 200
    with TestClient(create_app(runtime_factory=FakeRuntime,
                              multi_stop_workspace_factory=lambda: workspace)) as client:
        record = client.get("/api/multi-stop/itineraries").json()[0]
        assert record["draft"] == draft
        assert record["selected_plan_id"] == plan["plan_id"]


def test_six_stops_rejected_before_querying_routes():
    data = request_data()
    for index in range(2):
        data["stops"].insert(-1, {**data["stops"][1], "stop_id": f"extra-{index}"})
    service = AsyncMock()
    with TestClient(create_app(runtime_factory=FakeRuntime,
                              multi_stop_service_factory=lambda: service)) as client:
        assert client.post("/api/multi-stop/plan", json=data).status_code == 422
    service.plan.assert_not_awaited()


def test_place_parser_keeps_poi_identity_and_consistent_same_city_codes():
    result = AmapPlaceService._parse_results(
        {
            "status": "1",
            "pois": [
                {
                    "name": "示例",
                    "location": "114.1,22.6",
                    "adcode": "440305",
                    "id": "poi-1",
                }
            ],
        },
        "示例",
    )
    assert result.places[0].city_code == "440300"
    assert result.places[0].provider_place_id == "poi-1"
    assert AmapPlaceService._city_code("110101") == "110000"
    assert AmapPlaceService._city_code(None) is None


async def test_multi_tool_card_has_validated_draft_and_does_not_expose_entire_payload(tmp_path):
    draft = await MultiStopWorkspace(tmp_path / "multi.sqlite").create_draft(fixture_result())
    card = result_card_from_tool_message(
        ToolMessage(
            content=draft.model_dump_json(),
            name="plan_multi_stop_trip",
            tool_call_id="multi",
        )
    )
    assert card.type == "multi_stop"
    assert card.draft_id == draft.draft_id
    assert card.stop_names == [stop.name for stop in draft.result.request.stops]
    assert "segments" not in card.model_dump()


def test_multi_snapshot_cannot_reuse_older_single_snapshot_or_single_save_tool():
    old = AIMessage(content="", additional_kwargs={"travel_planning_payload": {"old": True}})
    multi = ToolMessage(content="{}", name="plan_multi_stop_trip", tool_call_id="multi")
    messages = [old, multi]
    assert latest_planning_payload(messages) is None
    assert (
        PreferenceReplanningMiddleware().before_model(
            {
                "messages": [
                    *messages,
                    HumanMessage(content="改成少步行"),
                ]
            },
            None,
        )
        is None
    )
    save = AIMessage(
        id="save-call",
        content="",
        tool_calls=[
            {
                "name": "save_itinerary",
                "id": "save",
                "args": {},
            }
        ],
    )
    result = ItinerarySaveGroundingMiddleware().after_model({"messages": [*messages, save]}, None)
    assert result["messages"][0].id == "save-call"
    assert result["messages"][0].tool_calls == []


async def test_multi_mcp_tool_is_exposed_and_returns_persistent_draft(monkeypatch, tmp_path):
    import json

    from travel_agent.mcp_servers import planning_server

    service = AsyncMock()
    service.plan.return_value = fixture_result()
    workspace = MultiStopWorkspace(tmp_path / "mcp.sqlite")
    monkeypatch.setattr(planning_server, "MultiStopPlanningService", lambda: service)
    monkeypatch.setattr(planning_server, "MultiStopWorkspace", lambda: workspace)
    tools = await planning_server.mcp.list_tools()
    assert "plan_multi_stop_trip" in [tool.name for tool in tools]
    response = await planning_server.plan_multi_stop_trip(fixture_result().request)
    draft = json.loads(response.content[0].text)
    from uuid import UUID

    assert (
        await workspace.get_draft(UUID(draft["draft_id"]))
    ).result.selection.status == "feasible"


async def test_arrival_routing_does_not_force_single_leg_on_multi_request():
    from tests.middleware.test_arrival_deadline_routing import build_request, capture_request
    from travel_agent.middleware.arrival_deadline_routing import ArrivalDeadlineRoutingMiddleware

    request = build_request([HumanMessage(content="明天从A先到B再去C，13点前到达")])
    response = await ArrivalDeadlineRoutingMiddleware().awrap_model_call(request, capture_request)
    assert response is request


async def test_manual_segment_selection_preserves_recommendation_and_saves_whole_choice(tmp_path):
    workspace = MultiStopWorkspace(tmp_path / "select.sqlite")
    draft = await workspace.create_draft(fixture_result())
    recommended = draft.result.selection.recommended_plan_id
    changed = await workspace.select(draft.draft_id, ["bus", "bus"])
    assert changed.draft_id != draft.draft_id
    assert changed.result.selection.recommended_plan_id == recommended
    selected = changed.result.selection.selected_plan_id
    adopted = next(plan for plan in changed.result.selection.plans if plan.plan_id == selected)
    assert [item.leg.candidate_id for item in adopted.evaluation.legs] == ["bus", "bus"]
    assert selected != recommended
    saved = await workspace.save(changed.draft_id, selected)
    assert saved.selected_plan_id == selected
    assert (await workspace.get_draft(draft.draft_id)) == draft


async def test_invalid_manual_combination_keeps_original_draft_untouched(tmp_path):
    import pytest

    workspace = MultiStopWorkspace(tmp_path / "select.sqlite")
    draft = await workspace.create_draft(fixture_result())
    with pytest.raises(ValueError, match="无法作为可行行程"):
        await workspace.select(draft.draft_id, ["drive", "bus"])
    with pytest.raises(ValueError, match="不存在"):
        await workspace.select(draft.draft_id, ["invented", "bus"])
    assert (await workspace.get_draft(draft.draft_id)) == draft
