from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tests.api.test_multi_stop import fixture_result
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport, StopWeather
from travel_agent.services.departure_watch import DepartureWatch
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


async def setup(tmp_path):
    workspace = MultiStopWorkspace(tmp_path / "watch.sqlite")
    result = fixture_result()
    draft = await workspace.create_draft(result)
    saved = await workspace.save(draft.draft_id, result.selection.recommended_plan_id)
    departure = result.request.departure_time.departure_at
    planner, weather = AsyncMock(), AsyncMock()
    planner.plan.return_value = result
    plan = result.selection.plans[0]
    weather.report.return_value = MultiStopWeatherReport(
        plan_id=plan.plan_id,
        queried_at=departure - timedelta(minutes=20),
        stops=[
            StopWeather(
                stop_id=s.stop_id,
                name=s.name,
                target_at=s.arrival_at,
                status="forecast",
                warnings=["降水提示"],
            )
            for s in plan.evaluation.stops
        ],
        limitations=[],
    )
    return workspace, saved, departure, planner, weather


@pytest.mark.asyncio
async def test_restart_due_idempotency_and_immutable_save(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    service = DepartureWatch(workspace, planner, weather)
    before = departure - timedelta(hours=1)
    first = await service.enable(saved.itinerary_id, now=before)
    assert (await service.enable(saved.itinerary_id, now=before))["id"] == first["id"]
    await service.tick(now=before)
    planner.plan.assert_not_awaited()
    restarted = DepartureWatch(MultiStopWorkspace(workspace.path), planner, weather)
    await restarted.tick(now=departure - timedelta(minutes=20))
    row = (await restarted.list())[0]
    assert row["status"] == "completed" and "降水提示" in row["message"]
    await restarted.tick(now=departure - timedelta(minutes=10))
    planner.plan.assert_awaited_once()
    assert (await workspace.list_saved())[0] == saved


@pytest.mark.asyncio
async def test_failure_retry_limit_and_expiry(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    planner.plan.side_effect = RuntimeError("provider unavailable")
    service = DepartureWatch(workspace, planner, weather)
    await service.enable(saved.itinerary_id, now=departure - timedelta(hours=1))
    for minutes in [25, 20, 15]:
        await service.tick(now=departure - timedelta(minutes=minutes))
    row = (await service.list())[0]
    assert row["status"] == "failed" and row["attempts"] == 3
    assert "不能判断" in row["message"]
    await service.tick(now=departure)
    assert planner.plan.await_count == 3


@pytest.mark.asyncio
async def test_cancel_inflight_and_expired_never_check(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    service = DepartureWatch(workspace, planner, weather)
    row = await service.enable(saved.itinerary_id, now=departure - timedelta(hours=1))
    from uuid import UUID

    claimed = service._claim(departure - timedelta(minutes=20))
    assert claimed
    await service.cancel(UUID(row["id"]))
    service._finish(row["id"], "completed", "stale result", datetime.now(UTC))
    assert (await service.list())[0]["status"] == "cancelled"
    await service.tick(now=departure)
    planner.plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_requires_saved_future_scheduled_plan(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    service = DepartureWatch(workspace, planner, weather)
    with pytest.raises(ValueError):
        await service.enable(saved.itinerary_id, now=departure)
    result = fixture_result()
    result.request.departure_time = None
    result.request.arrival_deadline = None
    for stop in result.request.stops:
        stop.earliest_arrival_at = None
        stop.latest_arrival_at = None
    draft = await workspace.create_draft(result)
    relative = await workspace.save(draft.draft_id, result.selection.recommended_plan_id)
    with pytest.raises(ValueError, match="出发时间"):
        await service.enable(relative.itinerary_id)


@pytest.mark.asyncio
async def test_claim_lease_and_restart_recovery(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    service = DepartureWatch(workspace, planner, weather)
    now = departure - timedelta(minutes=20)
    await service.enable(saved.itinerary_id, now=now)
    assert service._claim(now)
    assert service._claim(now) is None
    await DepartureWatch(workspace, planner, weather).tick(now=now + timedelta(minutes=4))
    assert (await service.list())[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_unknown_weather_is_not_a_successful_check(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    weather.report.return_value.stops[0].status = "unavailable"
    service = DepartureWatch(workspace, planner, weather)
    now = departure - timedelta(minutes=20)
    await service.enable(saved.itinerary_id, now=now)
    await service.tick(now=now)
    assert (await service.list())[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_slow_same_mode_estimate_produces_notice(tmp_path):
    workspace, saved, departure, planner, weather = await setup(tmp_path)
    updated = planner.plan.return_value.model_copy(deep=True)
    for plan in updated.selection.plans:
        plan.evaluation.totals.elapsed_s += 3600
    planner.plan.return_value = updated
    service = DepartureWatch(workspace, planner, weather)
    now = departure - timedelta(minutes=20)
    await service.enable(saved.itinerary_id, now=now)
    await service.tick(now=now)
    assert "增加约60分钟" in (await service.list())[0]["message"]
