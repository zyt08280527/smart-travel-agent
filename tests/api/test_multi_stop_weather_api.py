import asyncio
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from tests.services.test_multi_stop_weather import NOW, draft
from travel_agent.api.app import create_app
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def test_weather_api_uses_stored_draft_and_selected_id_without_modifying_it(tmp_path):
    value = draft()
    workspace = MultiStopWorkspace(tmp_path / "weather.sqlite")
    workspace._write_draft(value.draft_id, value.model_dump_json())
    weather = AsyncMock()

    async def report(stored, selected):
        assert stored == value
        if selected not in [plan.plan_id for plan in stored.result.selection.plans]:
            raise ValueError("所选方案不属于当前可信草案")
        return MultiStopWeatherReport(plan_id=selected, queried_at=NOW, stops=[], limitations=[])

    weather.report.side_effect = report
    app = create_app(
        runtime_factory=FakeRuntime, multi_stop_workspace_factory=lambda: workspace,
        multi_stop_weather_factory=lambda: weather,
    )
    plan_id = value.result.selection.plans[0].plan_id
    path = f"/api/multi-stop/drafts/{value.draft_id}/weather"
    with TestClient(app) as client:
        assert client.post(path, json={"selected_plan_id": plan_id}).json()["plan_id"] == plan_id
        assert client.post(path, json={"selected_plan_id": "forged"}).status_code == 422
        assert client.post(path, json={
            "selected_plan_id": plan_id, "latitude": 23,
        }).status_code == 422
        stored = client.get(f"/api/multi-stop/drafts/{value.draft_id}").json()
        assert stored == value.model_dump(mode="json")


def test_saved_plan_weather_uses_original_snapshot_after_draft_choices_change(tmp_path):
    workspace = MultiStopWorkspace(tmp_path / "saved-weather.sqlite")
    value = draft()
    plan_id = value.result.selection.plans[0].plan_id
    workspace._write_draft(value.draft_id, value.model_dump_json())
    saved = asyncio.run(workspace.save(value.draft_id, plan_id))
    changed = value.model_copy(deep=True)
    changed.result.selection.plans = changed.result.selection.plans[1:]
    with workspace._database() as database:
        database.execute(
            "UPDATE multi_stop_drafts SET payload=? WHERE id=?",
            (changed.model_dump_json(), str(value.draft_id)),
        )
    resolved = asyncio.run(workspace.get_plan_draft(value.draft_id, plan_id))
    assert resolved == saved.draft
    assert asyncio.run(workspace.get_draft(value.draft_id)) == changed
