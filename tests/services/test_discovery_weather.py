import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.services.test_discovery import NOW, discovery_request, planned
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport, StopWeather
from travel_agent.services.discovery import DiscoveryService


def report_for(draft, plan_id, *, now, status="forecast", warnings=None):
    selected = next(p for p in draft.result.selection.plans if p.plan_id == plan_id)
    return MultiStopWeatherReport(
        plan_id=plan_id, queried_at=now, limitations=["仅对应到达采样点"],
        stops=[StopWeather(
            stop_id=stop.stop_id, name=stop.name, target_at=stop.arrival_at,
            status=status, forecast_at=stop.arrival_at if status == "forecast" else None,
            temperature_c=25 if status == "forecast" else None,
            apparent_temperature_c=25 if status == "forecast" else None,
            precipitation_mm=0 if status == "forecast" else None,
            wind_speed_kmh=10 if status == "forecast" else None,
            condition="阴天" if status == "forecast" else None,
            warnings=warnings or [],
        ) for stop in selected.evaluation.stops],
    )


def services():
    planner, weather = AsyncMock(), AsyncMock()
    planner.plan.side_effect = planned
    return planner, weather


@pytest.mark.asyncio
async def test_weather_disabled_preserves_old_flow_without_external_weather_calls():
    planner, weather = services()
    result = await DiscoveryService(planner, weather).recommend(discovery_request(), now=NOW)
    assert len(result.candidates) == 3
    assert all(item.weather is None for item in result.candidates)
    weather.report.assert_not_awaited()


@pytest.mark.asyncio
async def test_inform_keeps_uncertain_and_risky_candidates_with_original_timeline():
    planner, weather = services()

    def respond(draft, plan_id, *, now):
        return report_for(draft, plan_id, now=now, status="unavailable")

    weather.report.side_effect = respond
    result = await DiscoveryService(planner, weather).recommend(
        discovery_request(weather_policy="inform"), now=NOW,
    )
    assert len(result.candidates) == 3
    assert weather.report.await_count == 3
    for candidate in result.candidates:
        assert candidate.weather.stops[1].target_at == (
            candidate.request.departure_time.departure_at
            + timedelta(minutes=10)
        )
        assert candidate.weather.stops[1].status == "unavailable"
        assert any("进入正式规划后" in warning for warning in candidate.warnings)


@pytest.mark.asyncio
async def test_strict_filter_excludes_weather_signals_and_unknown_but_keeps_clear_sample():
    planner, weather = services()

    def respond(draft, plan_id, *, now):
        poi = draft.result.request.stops[1].provider_place_id
        if poi == "B02F37WNJN":
            return report_for(draft, plan_id, now=now, warnings=["降水信号"])
        if poi == "B0LKCH16U2":
            return report_for(draft, plan_id, now=now, status="out_of_range")
        return report_for(draft, plan_id, now=now)

    weather.report.side_effect = respond
    result = await DiscoveryService(planner, weather).recommend(
        discovery_request(weather_policy="exclude_signals"), now=NOW,
    )
    assert [c.card.attraction_id for c in result.candidates] == ["sz-museum-jintian"]
    assert len(result.excluded) == 2
    assert any("天气未知" in item.reason for item in result.excluded)
    assert any("未比较其他路线" in item.reason for item in result.excluded)
    assert result.candidates[0].request.max_transport_cost_yuan is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.ConnectError("secret url"), TimeoutError("secret"), ValueError("secret"),
])
async def test_failed_weather_cannot_pass_strict_filter_or_leak_provider_errors(failure):
    planner, weather = services()
    weather.report.side_effect = failure
    result = await DiscoveryService(planner, weather).recommend(
        discovery_request(weather_policy="exclude_signals"), now=NOW,
    )
    assert not result.candidates
    assert len(result.excluded) == 3
    assert "secret" not in result.model_dump_json()
    assert all("天气未知" in item.reason for item in result.excluded)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["plan_id", "missing_stop", "target_time"])
async def test_mismatched_or_incomplete_report_is_not_treated_as_weather_clear(corruption):
    planner, weather = services()

    def respond(draft, plan_id, *, now):
        report = report_for(draft, plan_id, now=now)
        if corruption == "plan_id":
            report.plan_id = "another-plan"
        elif corruption == "missing_stop":
            report.stops.pop()
        else:
            report.stops[1].target_at = NOW
        return report

    weather.report.side_effect = respond
    result = await DiscoveryService(planner, weather).recommend(
        discovery_request(weather_policy="exclude_signals"), now=NOW,
    )
    assert not result.candidates


@pytest.mark.asyncio
async def test_prior_hard_constraints_are_not_relaxed_and_cancel_propagates():
    planner, weather = services()
    result = await DiscoveryService(planner, weather).recommend(
        discovery_request(require_confirmed_open=True, weather_policy="inform"), now=NOW,
    )
    assert not result.candidates
    planner.plan.assert_not_awaited()
    weather.report.assert_not_awaited()
    weather.report.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await DiscoveryService(planner, weather).recommend(
            discovery_request(weather_policy="inform"), now=NOW,
        )
