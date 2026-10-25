from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from tests.api.test_multi_stop import fixture_result
from travel_agent.domain.multi_stop import MultiStopDraft
from travel_agent.domain.multi_stop_weather import StopWeather
from travel_agent.services.multi_stop_weather import MultiStopWeatherService
from travel_agent.services.weather import FORECAST_URL

NOW = datetime(2026, 10, 23, 0, tzinfo=UTC)


def draft():
    from uuid import uuid4

    return MultiStopDraft(draft_id=uuid4(), created_at=NOW, result=fixture_result())


def payload(target, **updates):
    hour = target.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    data = {
        "hourly_units": {"temperature_2m": "°C", "apparent_temperature": "°C",
                         "precipitation": "mm", "wind_speed_10m": "km/h"},
        "hourly": {
            "time": [int(hour.timestamp()), int((hour + timedelta(hours=1)).timestamp())],
            "temperature_2m": [30, 31], "apparent_temperature": [36, 36],
            "precipitation": [2, 2], "wind_speed_10m": [45, 45], "weather_code": [95, 95],
        },
    }
    data["hourly"].update(updates)
    return data


def base(target=None):
    return StopWeather(
        stop_id="visit", name="到访点", target_at=target or datetime(2026, 10, 24, 1, tzinfo=UTC),
        status="unavailable",
    )


def test_hour_match_has_aware_time_units_and_deterministic_hints():
    record = base()
    weather = MultiStopWeatherService._match(payload(record.target_at), record)
    assert weather.status == "forecast"
    assert weather.forecast_at == record.target_at
    assert weather.precipitation_mm == 2
    assert len(weather.warnings) == 4
    assert record.status == "unavailable"


def test_nearest_hour_tie_uses_earlier_hour_and_large_gap_is_not_forecast():
    target = datetime(2026, 10, 24, 1, 30, tzinfo=UTC)
    weather = MultiStopWeatherService._match(payload(target), base(target))
    assert weather.forecast_at.minute == 0 and weather.forecast_at.hour == 1
    bad = payload(target)
    bad["hourly"]["time"] = [int((target - timedelta(hours=3)).timestamp())]
    with pytest.raises(ValueError, match="距离"):
        MultiStopWeatherService._match(bad, base(target))


@pytest.mark.parametrize("changes", [
    {"precipitation": [None, None]}, {"temperature_2m": [float("nan"), 30]},
    {"wind_speed_10m": [-1, -1]}, {"weather_code": [None, None]}, {"time": []},
])
def test_missing_or_invalid_data_does_not_become_good_weather(changes):
    record = base()
    with pytest.raises((ValueError, TypeError)):
        MultiStopWeatherService._match(payload(record.target_at, **changes), record)


@pytest.mark.asyncio
@respx.mock
async def test_trusted_plan_uses_point_forecasts_deduplicates_and_preserves_snapshot():
    value = draft()
    plan = value.result.selection.plans[0]
    # Same point/date at two visit occurrences still produces two display rows.
    value.result.request.stops[-1].point = value.result.request.stops[0].point.model_copy()
    value.result.request.stops[1].point.latitude += 0.01
    for timing in plan.evaluation.stops:
        timing.arrival_at = datetime(2026, 10, 24, 1, 10, tzinfo=UTC)
    original = value.model_dump_json()
    route = respx.get(FORECAST_URL).mock(return_value=httpx.Response(
        200, json=payload(plan.evaluation.stops[0].arrival_at),
    ))
    async with httpx.AsyncClient() as client:
        report = await MultiStopWeatherService(client).report(value, plan.plan_id, now=NOW)
    assert route.call_count == 2
    assert len(report.stops) == 3
    assert all(stop.status == "forecast" for stop in report.stops)
    assert "precipitation_unit=mm" in str(route.calls[0].request.url)
    assert "timeformat=unixtime" in str(route.calls[0].request.url)
    assert value.model_dump_json() == original


@pytest.mark.asyncio
@respx.mock
async def test_provider_failure_and_unit_mismatch_stay_unknown_without_error_details():
    value = draft()
    plan = value.result.selection.plans[0]
    respx.get(FORECAST_URL).mock(side_effect=httpx.ConnectError("secret key=url"))
    async with httpx.AsyncClient() as client:
        report = await MultiStopWeatherService(client).report(value, plan.plan_id, now=NOW)
    assert all(stop.status == "unavailable" for stop in report.stops)
    assert "secret" not in report.model_dump_json()
    data = payload(base().target_at)
    data["hourly_units"]["temperature_2m"] = "°F"
    with pytest.raises(ValueError, match="单位"):
        MultiStopWeatherService._match(data, base())


@pytest.mark.asyncio
@respx.mock
async def test_past_and_out_of_range_never_query_current_weather():
    value = draft()
    plan = value.result.selection.plans[0]
    plan.evaluation.stops[0].arrival_at = NOW - timedelta(hours=1)
    for timing in plan.evaluation.stops[1:]:
        timing.arrival_at = NOW + timedelta(days=17)
    async with httpx.AsyncClient() as client:
        report = await MultiStopWeatherService(client).report(value, plan.plan_id, now=NOW)
        with pytest.raises(ValueError, match="可信"):
            await MultiStopWeatherService(client).report(value, "forged", now=NOW)
    assert [stop.status for stop in report.stops] == ["past", "out_of_range", "out_of_range"]
    assert not respx.calls


def test_nearest_hour_can_cross_beijing_midnight_without_naive_timezone_guessing():
    from travel_agent.services.multi_stop_weather import SHANGHAI

    target = datetime(2026, 10, 24, 15, 50, tzinfo=UTC)  # Beijing 23:50
    result = MultiStopWeatherService._match(payload(target), base(target))
    assert result.forecast_at == datetime(2026, 10, 24, 16, tzinfo=UTC)
    assert result.forecast_at.astimezone(SHANGHAI).day == 25


@pytest.mark.asyncio
@respx.mock
async def test_one_failed_point_does_not_erase_other_stops():
    value = draft()
    plan = value.result.selection.plans[0]
    value.result.request.stops[1].point.latitude = 25
    for timing in plan.evaluation.stops:
        timing.arrival_at = base().target_at

    def respond(request):
        if float(request.url.params["latitude"]) == 25:
            return httpx.Response(503)
        return httpx.Response(200, json=payload(base().target_at))

    respx.get(FORECAST_URL).mock(side_effect=respond)
    async with httpx.AsyncClient() as client:
        result = await MultiStopWeatherService(client).report(value, plan.plan_id, now=NOW)
    assert [stop.status for stop in result.stops] == ["forecast", "unavailable", "forecast"]
async def test_untimed_plan_refuses_forecast_without_external_requests():
    value = draft()
    value.result.request.departure_time = None
    value.result.request.arrival_deadline = None
    with pytest.raises(ValueError, match="出发时间"):
        await MultiStopWeatherService().report(
            value, value.result.selection.plans[0].plan_id, now=NOW)
