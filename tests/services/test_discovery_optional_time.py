from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tests.services.test_discovery import NOW, discovery_request, planned
from travel_agent.services.discovery import DiscoveryService


async def test_untimed_discovery_preserves_round_trip_budget_stay_and_duration_limit():
    planner = AsyncMock()
    planner.plan.side_effect = planned
    result = await DiscoveryService(planner).recommend(
        discovery_request(departure_at=None, budget_yuan=4), now=NOW)
    assert len(result.candidates) == 2
    for item in result.candidates:
        assert item.request.departure_time is None
        assert item.request.arrival_deadline is None
        assert item.request.max_elapsed_minutes == 240
        assert item.totals.elapsed_s == 170 * 60
        assert item.request.stops[0].provider_place_id == item.request.stops[-1].provider_place_id


async def test_untimed_discovery_excludes_overlong_routes_without_fabricated_deadline():
    planner = AsyncMock()
    planner.plan.side_effect = planned
    result = await DiscoveryService(planner).recommend(
        discovery_request(departure_at=None, available_minutes=130), now=NOW)
    assert not result.candidates
    assert len(result.excluded) == 3
    assert all(call.args[0].departure_time is None for call in planner.plan.await_args_list)


@pytest.mark.parametrize("policy", ["inform", "exclude_signals"])
def test_arrival_weather_requires_time_only_if_explicitly_enabled(policy):
    with pytest.raises(ValidationError, match="出发时间"):
        discovery_request(departure_at=None, weather_policy=policy)
