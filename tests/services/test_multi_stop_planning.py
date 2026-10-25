import asyncio
from datetime import datetime

import pytest

from tests.middleware.test_preference_replanning import direct_transit_payload
from tests.services.test_multi_stop_timeline import request_data
from travel_agent.domain.multi_stop import MultiStopRequest
from travel_agent.domain.route import RoutePlan
from travel_agent.domain.transit import TransitPlan
from travel_agent.services.multi_stop_planning import MultiStopPlanningService
from travel_agent.services.multi_stop_selection import MultiStopSearchBudgetError


class FixtureProviders:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.peak = 0
        self.fail = set()
        self.delay = 0.002
        self.started = asyncio.Event()

    async def _query(self, mode, origin, destination):
        self.calls.append((mode, origin, destination))
        self.started.set()
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            if mode in self.fail:
                raise RuntimeError("https://provider.example/route?key=DO-NOT-EXPOSE")
        finally:
            self.active -= 1

    async def plan_driving_route(self, origin, destination):
        await self._query("driving", origin, destination)
        return RoutePlan(mode="driving", origin=origin, destination=destination,
                         distance_m=1000, duration_s=600, tolls_yuan=2,
                         geometry=[origin, destination], attribution="fixed driving fixture")

    async def plan_walking_route(self, origin, destination):
        await self._query("walking", origin, destination)
        return RoutePlan(mode="walking", origin=origin, destination=destination,
                         distance_m=1000, duration_s=1800,
                         geometry=[origin, destination], attribution="fixed walking fixture")

    async def plan_transit_route(self, origin, destination, *, strategy, alternative_routes):
        await self._query("transit", origin, destination)
        payload = direct_transit_payload()
        payload.update(origin=origin.model_dump(), destination=destination.model_dump(),
                       strategy=strategy)
        payload["options"] = payload["options"][:alternative_routes]
        return TransitPlan.model_validate(payload)


def make_service(providers, **kwargs):
    return MultiStopPlanningService(
        driving_service=providers, walking_service=providers, transit_service=providers,
        clock=lambda: datetime.fromisoformat("2026-10-22T12:00:00+08:00"), **kwargs,
    )


def make_request(**prefs):
    data = request_data()
    for index, stop in enumerate(data["stops"]):
        stop["point"]["latitude"] += index / 100
    data["preferences"] = prefs
    return MultiStopRequest.model_validate(data)


async def test_only_queries_adjacent_pairs_and_respects_shared_concurrency_budget():
    providers = FixtureProviders()
    request = make_request()
    result = await make_service(providers, concurrency=2).plan(request)
    assert result.selection.status == "feasible"
    assert result.route_service_calls == len(providers.calls) == 9
    assert providers.peak == 2
    expected = [(request.stops[index].point, request.stops[index + 1].point) for index in range(3)]
    assert all((origin, destination) in expected for _, origin, destination in providers.calls)
    assert len(result.segments) == 3
    for segment in result.segments:
        assert len(segment.candidates) == 4
        assert all(leg.source == "amap" for leg in segment.candidates)
        assert all(leg.fetched_at.hour == 12 for leg in segment.candidates)
        assert sum(leg.transit_details is not None for leg in segment.candidates) == 2
        driving = next(leg for leg in segment.candidates if leg.option.mode == "driving")
        assert driving.option.cost_yuan is None
        assert driving.option.tolls_yuan == 2
        assert driving.route_details.geometry


async def test_no_driving_calls_when_user_cannot_drive_and_all_transit_candidates_survive():
    providers = FixtureProviders()
    request = make_request(can_drive=False, priority="subway_first")
    request.arrival_deadline = None
    result = await make_service(providers).plan(request)
    assert result.route_service_calls == 6
    assert all(mode != "driving" for mode, _, _ in providers.calls)
    assert result.selection.plans[0].subway_leg_count == 3
    assert all(len(segment.candidates) == 3 for segment in result.segments)
    assert all(item.leg.transit_details is not None
               for item in result.selection.plans[0].evaluation.legs)


async def test_call_budget_is_validated_before_any_provider_calls():
    providers = FixtureProviders()
    with pytest.raises(MultiStopSearchBudgetError):
        await make_service(providers).plan(make_request(), max_route_calls=8)
    assert providers.calls == []


async def test_one_mode_failure_keeps_other_routes_and_never_exposes_exception_urls():
    providers = FixtureProviders()
    providers.fail = {"transit"}
    result = await make_service(providers).plan(make_request(can_drive=False))
    assert result.selection.status == "feasible"
    assert result.selection.plans[0].evaluation.totals.total_cost_yuan == 0
    assert all(item.leg.option.mode == "walking"
               for item in result.selection.plans[0].evaluation.legs)
    assert "DO-NOT-EXPOSE" not in result.model_dump_json()
    assert result.selection.insufficient_combinations > 0


async def test_all_providers_fail_returns_insufficient_data_and_no_invented_plan():
    providers = FixtureProviders()
    providers.fail = {"walking", "driving", "transit"}
    result = await make_service(providers).plan(make_request())
    assert result.selection.status == "insufficient_data"
    assert result.selection.plans == []
    assert result.selection.recommended_plan_id is None
    assert result.selection.issue_examples[0].missing_data


async def test_timeout_cancels_queries_without_generating_fake_routes():
    providers = FixtureProviders()
    providers.delay = 1
    result = await make_service(providers, timeout_seconds=0.001).plan(make_request())
    assert result.selection.status == "insufficient_data"
    assert providers.active == 0
    assert all("超时" in leg.option.failure_reason
               for segment in result.segments for leg in segment.candidates)


async def test_cancellation_propagates_instead_of_becoming_a_degraded_success():
    providers = FixtureProviders()
    providers.delay = 10
    task = asyncio.create_task(make_service(providers).plan(make_request()))
    await providers.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert providers.active == 0


async def test_provider_response_mode_mismatch_is_reported_as_failure():
    providers = FixtureProviders()
    providers.plan_driving_route = providers.plan_walking_route
    result = await make_service(providers).plan(make_request())
    assert result.selection.status == "feasible"
    assert all("格式不匹配" in leg.option.failure_reason
               for segment in result.segments for leg in segment.candidates
               if leg.option.mode == "driving")


async def test_invalid_planning_limits_do_not_start_provider_queries():
    providers = FixtureProviders()
    service = make_service(providers)
    for kwargs in ({"transit_alternatives": 7}, {"limit": 0}, {"max_route_calls": 0}):
        with pytest.raises(ValueError):
            await service.plan(make_request(), **kwargs)
    assert providers.calls == []
async def test_untimed_request_queries_all_adjacent_segments_and_selects_full_trip():
    request = make_request()
    request.departure_time = None
    request.arrival_deadline = None
    providers = FixtureProviders()
    result = await make_service(providers).plan(request)
    assert result.request.departure_time is None
    assert len(result.segments) == 3
    assert len(providers.calls) == 9
    assert result.selection.status == "feasible"
    assert all(plan.evaluation.timing_mode == "relative" for plan in result.selection.plans)
    assert all(len(plan.evaluation.legs) == 3 for plan in result.selection.plans)
