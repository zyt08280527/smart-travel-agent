import asyncio

import pytest

from travel_agent.domain.decision import TravelPreferences
from travel_agent.domain.route import GeoPoint, RoutePlan, TrafficSegment
from travel_agent.domain.transit import TransitLeg, TransitOption, TransitPlan
from travel_agent.domain.weather import CurrentWeather, Location
from travel_agent.services.travel_planning import TravelPlanningService

ORIGIN = GeoPoint(latitude=22.5359, longitude=113.9315)
DESTINATION = GeoPoint(latitude=22.6009, longitude=113.9879)


class ConcurrencyGate:
    def __init__(self) -> None:
        self.started = 0
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self) -> None:
        self.started += 1
        if self.started == 4:
            self.all_started.set()
        await self.release.wait()


def build_weather() -> CurrentWeather:
    return CurrentWeather(
        location=Location(
            name="深圳",
            country="中国",
            admin1="广东",
            latitude=22.54,
            longitude=114.06,
        ),
        temperature_c=25,
        apparent_temperature_c=26,
        precipitation_mm=0,
        wind_speed_kmh=8,
        weather_code=0,
        condition="晴朗",
        observed_at="2026-09-11T10:00",
    )


def build_driving() -> RoutePlan:
    return RoutePlan(
        mode="driving",
        origin=ORIGIN,
        destination=DESTINATION,
        distance_m=10_000,
        duration_s=1_800,
        duration_basis="traffic_aware_estimate",
        tolls_yuan=0,
        taxi_cost_yuan=38,
        traffic_segments=[TrafficSegment(status="畅通", distance_m=10_000)],
        attribution="amap driving",
    )


def build_walking() -> RoutePlan:
    return RoutePlan(
        mode="walking",
        origin=ORIGIN,
        destination=DESTINATION,
        distance_m=5_000,
        duration_s=3_600,
        attribution="walking provider",
    )


def build_transit() -> TransitPlan:
    return TransitPlan(
        origin=ORIGIN,
        destination=DESTINATION,
        origin_city_code="0755",
        destination_city_code="0755",
        options=[
            TransitOption(
                distance_m=8_000,
                duration_s=2_400,
                walking_distance_m=500,
                cost_yuan=5,
                transfer_count=1,
                legs=[TransitLeg(mode="walking", distance_m=500)],
            )
        ],
        attribution="amap transit",
    )


class FakeWeatherService:
    def __init__(self, gate: ConcurrencyGate | None = None) -> None:
        self.gate = gate

    async def get_current_weather(self, _city: str) -> CurrentWeather:
        if self.gate:
            await self.gate.wait()
        return build_weather()


class FakeRouteService:
    def __init__(
        self,
        result: RoutePlan | Exception,
        gate: ConcurrencyGate | None = None,
    ) -> None:
        self.result = result
        self.gate = gate

    async def plan_driving_route(
        self, _origin: GeoPoint, _destination: GeoPoint
    ) -> RoutePlan:
        if self.gate:
            await self.gate.wait()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def plan_walking_route(
        self, _origin: GeoPoint, _destination: GeoPoint
    ) -> RoutePlan:
        return await self.plan_driving_route(_origin, _destination)


class FakeTransitService:
    def __init__(self, gate: ConcurrencyGate | None = None) -> None:
        self.gate = gate

    async def plan_transit_route(
        self, _origin: GeoPoint, _destination: GeoPoint
    ) -> TransitPlan:
        if self.gate:
            await self.gate.wait()
        return build_transit()


def build_service(
    *,
    gate: ConcurrencyGate | None = None,
    driving_result: RoutePlan | Exception | None = None,
) -> TravelPlanningService:
    return TravelPlanningService(
        weather_service=FakeWeatherService(gate),
        driving_service=FakeRouteService(
            driving_result if driving_result is not None else build_driving(),
            gate,
        ),
        walking_service=FakeRouteService(build_walking(), gate),
        transit_service=FakeTransitService(gate),
    )


@pytest.mark.asyncio
async def test_compare_starts_weather_and_three_route_calls_concurrently() -> None:
    gate = ConcurrencyGate()
    task = asyncio.create_task(
        build_service(gate=gate).compare(
            city="深圳",
            origin_name="粤海校区",
            destination_name="丽湖校区",
            origin=ORIGIN,
            destination=DESTINATION,
        )
    )

    await asyncio.wait_for(gate.all_started.wait(), timeout=1)
    assert gate.started == 4
    gate.release.set()
    result = await task

    assert len(result.recommendation.ranked_options) == 3


@pytest.mark.asyncio
async def test_compare_normalizes_provider_results_before_scoring() -> None:
    result = await build_service().compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        preferences=TravelPreferences(priority="fastest"),
    )

    assert result.context.weather is not None
    assert result.recommendation.recommended_mode == "driving"
    driving = next(
        item.option
        for item in result.recommendation.ranked_options
        if item.option.mode == "driving"
    )
    assert driving.duration_basis == "traffic_aware_estimate"
    assert driving.taxi_cost_yuan == 38
    assert driving.traffic_status_counts == {"畅通": 1}
    assert driving.cost_yuan is None
    assert "driving 方案缺少费用数据" in result.recommendation.limitations


@pytest.mark.asyncio
async def test_compare_degrades_when_one_route_provider_fails() -> None:
    result = await build_service(
        driving_result=RuntimeError("驾车接口超时")
    ).compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
    )

    assert len(result.recommendation.ranked_options) == 2
    assert result.recommendation.unavailable_options[0].mode == "driving"
    assert "驾车接口超时" in (
        result.recommendation.unavailable_options[0].failure_reason or ""
    )
