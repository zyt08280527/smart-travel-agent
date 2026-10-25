from datetime import timedelta
from uuid import UUID

import pytest

from tests.services.test_departure_watch import setup
from tests.services.test_single_departure_watch import NOW, saved_trip
from travel_agent.services.departure_watch import DepartureWatch
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


@pytest.mark.asyncio
async def test_read_receipt_restart_new_result_and_stale_receipt(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    service = DepartureWatch(workspace, planner, weather)
    now = departure - timedelta(minutes=25)
    task = await service.enable(saved.itinerary_id, now=now)
    assert not task["unread"]
    service._claim(now)
    service._finish(task["id"], "pending", "检查未完成", now)
    first = (await service.list())[0]
    assert first["unread"] and first["kind"] == "multi"
    await service.mark_read(first["id"], first["notification_token"])
    restarted = DepartureWatch(MultiStopWorkspace(workspace.path), planner, weather)
    assert not (await restarted.list())[0]["unread"]
    # A later retry must be unread again, even if its warning text is identical.
    later = now + timedelta(minutes=5)
    restarted._claim(later)
    restarted._finish(first["id"], "completed", "检查未完成", later)
    assert (await restarted.list())[0]["unread"]
    with pytest.raises(ValueError, match="新结果"):
        await restarted.mark_read(first["id"], first["notification_token"])
    original = await restarted.detail(UUID(first["id"]))
    assert original["snapshot"]["selected_plan_id"] == saved.selected_plan_id
    assert "不要保存" in original["replan_prompt"]
    assert (await workspace.list_saved())[0] == saved
    planner.plan.assert_not_awaited()
    weather.report.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_and_multi_are_listed_together_and_detail_keeps_constraints(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    service = DepartureWatch(workspace, planner, weather)
    await service.enable(saved.itinerary_id, now=departure - timedelta(minutes=40))
    _, single = await saved_trip(tmp_path)
    single.watch_context.preferences.max_walking_distance_m = 100
    single.watch_context.preferences.can_drive = False
    task = await service.enable_single(single, NOW + timedelta(hours=1), now=NOW)
    rows = await service.list()
    assert {row["kind"] for row in rows} == {"single", "multi"}
    detail = await service.detail(UUID(task["id"]))
    assert detail["snapshot"]["watch_context"]["preferences"]["max_walking_distance_m"] == 100
    assert "步行不超过100米" in detail["replan_prompt"]
    assert "不能驾车" in detail["replan_prompt"]
    assert "先向我确认" in detail["replan_prompt"]
    with pytest.raises(LookupError):
        await service.mark_read("missing", "a" * 64)
    assert len(await service.list()) == 2
