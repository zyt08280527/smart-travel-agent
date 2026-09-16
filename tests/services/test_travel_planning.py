import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from travel_agent.domain.decision import TravelPreferences
from travel_agent.domain.journey_time import ArrivalDeadline, DepartureTime
from travel_agent.domain.route import GeoPoint, RoutePlan, TrafficSegment
from travel_agent.domain.transit import TransitLeg, TransitOption, TransitPlan
from travel_agent.domain.weather import CurrentWeather, ForecastWeather, Location
from travel_agent.services.travel_decision import TravelDecisionError
from travel_agent.services.travel_planning import TravelPlanningService

ORIGIN = GeoPoint(latitude=22.5359, longitude=113.9315)
DESTINATION = GeoPoint(latitude=22.6009, longitude=113.9879)
SHANGHAI = ZoneInfo("Asia/Shanghai")
REFERENCE_TIME = datetime(2026, 9, 11, 10, 0, tzinfo=SHANGHAI)


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


def build_transit_with_alternatives() -> TransitPlan:
    return TransitPlan(
        origin=ORIGIN,
        destination=DESTINATION,
        origin_city_code="0755",
        destination_city_code="0755",
        options=[
            TransitOption(
                distance_m=8_000,
                duration_s=3_000,
                walking_distance_m=300,
                cost_yuan=4,
                transfer_count=0,
                legs=[TransitLeg(mode="walking", distance_m=300)],
            ),
            TransitOption(
                distance_m=9_000,
                duration_s=1_800,
                walking_distance_m=900,
                cost_yuan=7,
                transfer_count=2,
                legs=[TransitLeg(mode="walking", distance_m=900)],
            ),
        ],
        attribution="amap transit",
    )


def build_transit_with_bus_and_subway() -> TransitPlan:
    return TransitPlan(
        origin=ORIGIN,
        destination=DESTINATION,
        origin_city_code="0755",
        destination_city_code="0755",
        strategy=7,
        options=[
            TransitOption(
                distance_m=8_000,
                duration_s=2_000,
                walking_distance_m=200,
                cost_yuan=3,
                transfer_count=0,
                legs=[
                    TransitLeg(
                        mode="bus",
                        distance_m=7_800,
                        line_name="校巴通勤车",
                    )
                ],
            ),
            TransitOption(
                distance_m=9_000,
                duration_s=3_000,
                walking_distance_m=600,
                cost_yuan=5,
                transfer_count=1,
                legs=[
                    TransitLeg(
                        mode="subway",
                        distance_m=8_400,
                        line_name="地铁5号线",
                    )
                ],
            ),
        ],
        attribution="amap transit",
    )


class FakeWeatherService:
    def __init__(self, gate: ConcurrencyGate | None = None) -> None:
        self.gate = gate
        self.departure_calls: list[tuple[str, DepartureTime, datetime]] = []

    async def get_current_weather(self, _city: str) -> CurrentWeather:
        if self.gate:
            await self.gate.wait()
        return build_weather()

    async def get_weather_for_departure(
        self,
        city: str,
        departure: DepartureTime,
        *,
        reference_at: datetime,
    ) -> ForecastWeather:
        self.departure_calls.append((city, departure, reference_at))
        if self.gate:
            await self.gate.wait()
        return ForecastWeather(
            location=build_weather().location,
            temperature_c=28,
            apparent_temperature_c=29,
            precipitation_mm=1.2,
            wind_speed_kmh=12,
            weather_code=61,
            condition="小雨",
            forecast_at=departure.departure_at.isoformat(),
        )


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
        self,
        _origin: GeoPoint,
        _destination: GeoPoint,
        strategy: int = 0,
    ) -> TransitPlan:
        del strategy
        if self.gate:
            await self.gate.wait()
        return build_transit()


class FakeTransitAlternativesService:
    async def plan_transit_route(
        self,
        _origin: GeoPoint,
        _destination: GeoPoint,
        strategy: int = 0,
    ) -> TransitPlan:
        del strategy
        return build_transit_with_alternatives()


class FakeBusAndSubwayService:
    async def plan_transit_route(
        self,
        _origin: GeoPoint,
        _destination: GeoPoint,
        strategy: int = 0,
    ) -> TransitPlan:
        del strategy
        return build_transit_with_bus_and_subway()


class RecordingTransitService(FakeTransitService):
    strategy: int | None = None

    async def plan_transit_route(
        self,
        _origin: GeoPoint,
        _destination: GeoPoint,
        strategy: int = 0,
    ) -> TransitPlan:
        self.strategy = strategy
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
    assert len(result.recommendation_variants) == 5
    assert {
        variant.priority for variant in result.recommendation_variants
    } == {
        "balanced",
        "fastest",
        "cheapest",
        "least_walking",
        "fewest_transfers",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transit_strategy", "expected_provider_strategy"),
    [
        ("recommended", 0),
        ("fewest_transfers", 2),
        ("least_walking", 3),
        ("subway_first", 7),
    ],
)
async def test_compare_passes_public_transit_strategy_to_provider(
    transit_strategy: str,
    expected_provider_strategy: int,
) -> None:
    transit_service = RecordingTransitService()
    service = TravelPlanningService(
        weather_service=FakeWeatherService(),
        driving_service=FakeRouteService(build_driving()),
        walking_service=FakeRouteService(build_walking()),
        transit_service=transit_service,
    )

    await service.compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        preferences=TravelPreferences(
            transit_strategy=transit_strategy,  # type: ignore[arg-type]
        ),
    )

    assert transit_service.strategy == expected_provider_strategy


@pytest.mark.asyncio
async def test_subway_first_selects_subway_instead_of_faster_bus() -> None:
    service = TravelPlanningService(
        weather_service=FakeWeatherService(),
        driving_service=FakeRouteService(build_driving()),
        walking_service=FakeRouteService(build_walking()),
        transit_service=FakeBusAndSubwayService(),
    )

    result = await service.compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        preferences=TravelPreferences(transit_strategy="subway_first"),
    )

    assert result.selected_transit_candidate_index == 1
    selected = result.transit_candidates[result.selected_transit_candidate_index]
    assert any(leg.mode == "subway" for leg in selected.legs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("priority", "expected_duration_s", "expected_candidate_index"),
    [
        ("fastest", 1_800, 1),
        ("cheapest", 3_000, 0),
        ("least_walking", 3_000, 0),
        ("fewest_transfers", 3_000, 0),
    ],
)
async def test_compare_selects_transit_candidate_for_user_priority(
    priority: str,
    expected_duration_s: float,
    expected_candidate_index: int,
) -> None:
    service = TravelPlanningService(
        weather_service=FakeWeatherService(),
        driving_service=FakeRouteService(build_driving()),
        walking_service=FakeRouteService(build_walking()),
        transit_service=FakeTransitAlternativesService(),
    )

    result = await service.compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        preferences=TravelPreferences(priority=priority),  # type: ignore[arg-type]
    )

    transit = next(
        item.option
        for item in result.recommendation.ranked_options
        if item.option.mode == "transit"
    )
    assert transit.duration_s == expected_duration_s
    assert len(result.transit_candidates) == 2
    assert (
        result.selected_transit_candidate_index == expected_candidate_index
    )
    assert (
        result.transit_candidates[expected_candidate_index].duration_s
        == expected_duration_s
    )


@pytest.mark.asyncio
async def test_compare_records_route_snapshot_window() -> None:
    result = await build_service().compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        reference_at=REFERENCE_TIME,
        route_snapshot_ttl_seconds=300,
    )

    assert result.context.origin == ORIGIN
    assert result.context.destination == DESTINATION
    assert result.context.route_snapshot_at == REFERENCE_TIME
    assert result.context.route_snapshot_expires_at == REFERENCE_TIME + timedelta(
        minutes=5
    )


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


@pytest.mark.asyncio
async def test_compare_uses_departure_forecast_and_discloses_route_snapshot() -> None:
    weather_service = FakeWeatherService()
    service = TravelPlanningService(
        weather_service=weather_service,
        driving_service=FakeRouteService(build_driving()),
        walking_service=FakeRouteService(build_walking()),
        transit_service=FakeTransitService(),
    )
    departure = DepartureTime(
        departure_at=REFERENCE_TIME + timedelta(days=1, hours=5),
        timezone="Asia/Shanghai",
        precision="exact",
        source_text="明天下午三点",
    )

    result = await service.compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        departure_time=departure,
        reference_at=REFERENCE_TIME,
    )

    assert isinstance(result.context.weather, ForecastWeather)
    assert result.context.departure_time == departure
    assert weather_service.departure_calls == [
        ("深圳", departure, REFERENCE_TIME)
    ]
    assert any(
        "路线数据仍为查询时结果" in limitation
        for limitation in result.recommendation.limitations
    )


@pytest.mark.asyncio
async def test_compare_requires_reference_clock_with_departure_time() -> None:
    departure = DepartureTime(
        departure_at=REFERENCE_TIME + timedelta(hours=1),
        timezone="Asia/Shanghai",
        precision="exact",
        source_text="十一点",
    )

    with pytest.raises(ValueError, match="当前参考时间"):
        await build_service().compare(
            city="深圳",
            origin_name="粤海校区",
            destination_name="丽湖校区",
            origin=ORIGIN,
            destination=DESTINATION,
            departure_time=departure,
        )


@pytest.mark.asyncio
async def test_compare_back_calculates_latest_departures_from_arrival_goal() -> None:
    weather_service = FakeWeatherService()
    service = TravelPlanningService(
        weather_service=weather_service,
        driving_service=FakeRouteService(build_driving()),
        walking_service=FakeRouteService(build_walking()),
        transit_service=FakeTransitService(),
    )
    deadline = ArrivalDeadline(
        arrival_by=datetime(2026, 9, 11, 12, 0, tzinfo=SHANGHAI),
        precision="exact",
        source_text="今天中午12点前到",
        buffer_minutes=15,
    )

    result = await service.compare(
        city="深圳",
        origin_name="粤海校区",
        destination_name="丽湖校区",
        origin=ORIGIN,
        destination=DESTINATION,
        arrival_deadline=deadline,
        reference_at=REFERENCE_TIME,
    )

    assert result.context.arrival_deadline == deadline
    latest_by_mode = {
        item.option.mode: item.option.latest_departure_at
        for item in result.recommendation.ranked_options
    }
    assert latest_by_mode == {
        "driving": datetime(2026, 9, 11, 11, 15, tzinfo=SHANGHAI),
        "walking": datetime(2026, 9, 11, 10, 45, tzinfo=SHANGHAI),
        "transit": datetime(2026, 9, 11, 11, 5, tzinfo=SHANGHAI),
    }
    assert weather_service.departure_calls[0][1].departure_at == deadline.arrival_by
    assert any(
        "15 分钟缓冲反推" in limitation
        for limitation in result.recommendation.limitations
    )


@pytest.mark.asyncio
async def test_compare_rejects_departure_and_arrival_time_together() -> None:
    departure = DepartureTime(
        departure_at=REFERENCE_TIME + timedelta(hours=1),
        precision="exact",
        source_text="十一点出发",
    )
    deadline = ArrivalDeadline(
        arrival_by=REFERENCE_TIME + timedelta(hours=2),
        precision="exact",
        source_text="十二点前到",
    )

    with pytest.raises(ValueError, match="不能同时指定"):
        await build_service().compare(
            city="深圳",
            origin_name="粤海校区",
            destination_name="丽湖校区",
            origin=ORIGIN,
            destination=DESTINATION,
            departure_time=departure,
            arrival_deadline=deadline,
            reference_at=REFERENCE_TIME,
        )


@pytest.mark.asyncio
async def test_compare_rejects_arrival_goal_when_every_latest_departure_passed() -> None:
    deadline = ArrivalDeadline(
        arrival_by=REFERENCE_TIME + timedelta(minutes=10),
        precision="exact",
        source_text="十点十分前到",
        buffer_minutes=15,
    )

    with pytest.raises(TravelDecisionError, match="最晚出发时间已过"):
        await build_service().compare(
            city="深圳",
            origin_name="粤海校区",
            destination_name="丽湖校区",
            origin=ORIGIN,
            destination=DESTINATION,
            arrival_deadline=deadline,
            reference_at=REFERENCE_TIME,
        )
