import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tests.services.test_attraction import museum_place
from tests.services.test_multi_stop_selection import scenario
from tests.services.test_multi_stop_timeline import route_legs
from travel_agent.domain.discovery import DiscoveryRequest
from travel_agent.domain.multi_stop import LegCandidates, MultiStopPlanningResult, MultiStopRequest
from travel_agent.services.discovery import DiscoveryService
from travel_agent.services.multi_stop_selection import select_multi_stop_plans

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def discovery_request(**updates):
    return DiscoveryRequest.model_validate({
        "origin": museum_place().model_dump(), "departure_at": "2026-10-24T09:00:00+08:00",
        "available_minutes": 240, "stay_minutes": 120,
        "preferences": {"priority": "fastest", "can_drive": True}, **updates,
    })


def planned(request):
    groups = []
    for leg in route_legs(request):
        choices = []
        for identity, minutes, cost in [("fast", 10, 30), ("cheap", 25, 2)]:
            candidate = leg.model_copy(deep=True)
            candidate.candidate_id = identity
            candidate.option.mode = "transit"
            candidate.option.duration_s = minutes * 60
            candidate.option.cost_yuan = cost
            candidate.option.walking_distance_m = 100
            candidate.option.transfer_count = 0
            choices.append(candidate)
        groups.append(LegCandidates(
            origin_stop_id=leg.origin_stop_id, destination_stop_id=leg.destination_stop_id,
            candidates=choices,
        ))
    return MultiStopPlanningResult(
        request=request, segments=groups, selection=select_multi_stop_plans(request, groups),
        route_service_calls=4,
    )


@pytest.mark.asyncio
async def test_discovery_uses_only_curated_pois_round_trip_and_explicit_stay():
    planner = AsyncMock()
    planner.plan.side_effect = planned
    result = await DiscoveryService(planner).recommend(discovery_request(), now=NOW)
    assert len(result.candidates) == 3
    assert planner.plan.await_count == 3
    for item in result.candidates:
        assert item.request.stops[0].provider_place_id == item.request.stops[-1].provider_place_id
        assert item.request.stops[1].stay_minutes == 120
        assert item.request.stops[1].provider_place_id in {
            "B02F38IRTR", "B0LKCH16U2", "B02F37WNJN",
        }
        assert item.totals.elapsed_s == 140 * 60
        assert item.warnings
    nanshan = next(c for c in result.candidates if c.card.attraction_id == "sz-nanshan-museum")
    assert nanshan.estimated_transport_and_ticket_yuan is None


@pytest.mark.asyncio
async def test_budget_filters_unknown_ticket_and_finds_slower_affordable_route_before_ranking():
    planner = AsyncMock()
    planner.plan.side_effect = planned
    result = await DiscoveryService(planner).recommend(discovery_request(budget_yuan=4), now=NOW)
    assert len(result.candidates) == 2
    assert planner.plan.await_count == 2
    assert result.excluded[0].attraction_id == "sz-nanshan-museum"
    for item in result.candidates:
        assert item.estimated_transport_and_ticket_yuan == 4
        assert item.totals.elapsed_s == 170 * 60
        assert item.request.max_transport_cost_yuan == 4


@pytest.mark.asyncio
async def test_time_limit_cannot_be_relaxed_to_make_budget_work():
    planner = AsyncMock()
    planner.plan.side_effect = planned
    result = await DiscoveryService(planner).recommend(
        discovery_request(budget_yuan=4, available_minutes=150), now=NOW,
    )
    assert not result.candidates
    assert len(result.excluded) == 3


@pytest.mark.asyncio
async def test_strict_opening_and_expired_ticket_reject_before_route_calls():
    planner = AsyncMock()
    service = DiscoveryService(planner)
    strict = await service.recommend(discovery_request(require_confirmed_open=True), now=NOW)
    expired = await service.recommend(discovery_request(
        budget_yuan=100, departure_at="2026-11-01T09:00:00+08:00",
    ), now=NOW)
    assert not strict.candidates and not expired.candidates
    assert len(strict.excluded) == len(expired.excluded) == 3
    planner.plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_is_not_claimed_as_no_real_world_routes():
    planner = AsyncMock()
    planner.plan.side_effect = RuntimeError("secret provider details")
    result = await DiscoveryService(planner).recommend(discovery_request(), now=NOW)
    assert not result.candidates
    assert all("不能据此判断现实中没有路线" in item.reason for item in result.excluded)
    assert "secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_past_departure_rejects_without_queries():
    planner = AsyncMock()
    with pytest.raises(ValueError, match="未来"):
        await DiscoveryService(planner).recommend(
            discovery_request(), now=datetime(2026, 11, 1, tzinfo=UTC),
        )
    planner.plan.assert_not_awaited()


@pytest.mark.parametrize("updates", [
    {"origin": museum_place(city_code="440100").model_dump()},
    {"origin": museum_place(provider_place_id=None).model_dump()},
    {"available_minutes": 100}, {"budget_yuan": -1},
    {"departure_at": "2026-10-24T09:00:00"},
])
def test_invalid_scope_time_and_budget(updates):
    with pytest.raises(ValidationError):
        discovery_request(**updates)


def test_transport_budget_is_checked_for_all_combinations_and_manual_choices():
    request, groups = scenario("fastest")
    data = request.model_dump()
    data["max_transport_cost_yuan"] = 4
    planned_request = MultiStopRequest.model_validate(data)
    result = select_multi_stop_plans(planned_request, groups)
    assert result.plans and all(p.evaluation.totals.total_cost_yuan <= 4 for p in result.plans)
    with pytest.raises(ValueError):
        select_multi_stop_plans(planned_request, groups, selected_candidate_ids=["drive", "drive"])
    for group in groups:
        for leg in group.candidates:
            leg.option.cost_yuan = None
    unknown = select_multi_stop_plans(planned_request, groups)
    assert not unknown.plans
    assert "cost_unknown" in unknown.issue_counts


def test_optional_budget_preserves_legacy_plan_identifiers():
    request, groups = scenario()
    result = select_multi_stop_plans(request, groups)
    payload = request.model_dump(mode="json")
    payload.pop("max_transport_cost_yuan")
    payload.pop("max_elapsed_minutes")
    for plan in result.plans:
        old_identity = json.dumps({
            "request": payload,
            "legs": [item.leg.model_dump(mode="json") for item in plan.evaluation.legs],
        }, ensure_ascii=False, sort_keys=True)
        assert plan.plan_id == hashlib.sha256(old_identity.encode()).hexdigest()
