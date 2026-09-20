import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

from travel_agent.domain.decision import (
    JourneyContext,
    TravelComparisonResult,
    TravelOption,
    TravelPreferences,
)
from travel_agent.domain.journey_time import ArrivalDeadline, DepartureTime
from travel_agent.domain.route import GeoPoint, RoutePlan
from travel_agent.domain.transit import TransitPlan
from travel_agent.domain.weather import WeatherData
from travel_agent.services.amap_driving import AmapDrivingRouteService
from travel_agent.services.amap_walking import AmapWalkingRouteService
from travel_agent.services.transit import TransitService
from travel_agent.services.transit_selection import select_transit_candidate
from travel_agent.services.travel_decision import (
    recommend_travel_mode,
    recommend_travel_variants,
)
from travel_agent.services.weather import WeatherService

TRANSIT_STRATEGY_CODES = {
    "recommended": 0,
    "fewest_transfers": 2,
    "least_walking": 3,
    "subway_first": 7,
}


class TravelPlanningService:
    """Fetch weather and route candidates concurrently, then rank them."""

    def __init__(
        self,
        weather_service: WeatherService | None = None,
        driving_service: AmapDrivingRouteService | None = None,
        walking_service: AmapWalkingRouteService | None = None,
        transit_service: TransitService | None = None,
    ) -> None:
        self._weather_service = weather_service or WeatherService()
        self._driving_service = driving_service or AmapDrivingRouteService()
        self._walking_service = walking_service or AmapWalkingRouteService()
        self._transit_service = transit_service or TransitService()

    async def compare(
        self,
        *,
        city: str,
        origin_name: str,
        destination_name: str,
        origin: GeoPoint,
        destination: GeoPoint,
        preferences: TravelPreferences | None = None,
        departure_time: DepartureTime | None = None,
        arrival_deadline: ArrivalDeadline | None = None,
        reference_at: datetime | None = None,
        route_snapshot_ttl_seconds: int = 300,
    ) -> TravelComparisonResult:
        """Run four independent provider calls without serial wait time."""
        preferences = preferences or TravelPreferences()
        if not 0 <= route_snapshot_ttl_seconds <= 86400:
            raise ValueError("路线快照有效期必须在0到86400秒之间")
        if departure_time is not None and arrival_deadline is not None:
            raise ValueError("不能同时指定出发时间和最晚到达时间")
        if (
            departure_time is not None or arrival_deadline is not None
        ) and reference_at is None:
            raise ValueError("提供出发或到达时间时必须同时提供当前参考时间")
        weather_lookup_time = departure_time
        if arrival_deadline is not None:
            weather_lookup_time = DepartureTime(
                departure_at=arrival_deadline.arrival_by,
                timezone=arrival_deadline.timezone,
                precision=arrival_deadline.precision,
                source_text=arrival_deadline.source_text,
            )
        weather_call = (
            self._weather_service.get_current_weather(city)
            if weather_lookup_time is None
            else self._weather_service.get_weather_for_departure(
                city,
                weather_lookup_time,
                reference_at=reference_at,
            )
        )
        weather_result, driving_result, walking_result, transit_result = (
            await asyncio.gather(
                weather_call,
                self._driving_service.plan_driving_route(origin, destination),
                self._walking_service.plan_walking_route(origin, destination),
                self._transit_service.plan_transit_route(
                    origin,
                    destination,
                    strategy=TRANSIT_STRATEGY_CODES[
                        preferences.transit_strategy
                    ],
                ),
                return_exceptions=True,
            )
        )

        weather = (
            weather_result
            if isinstance(weather_result, WeatherData)
            else None
        )
        transit_option, selected_transit_candidate_index = self._transit_option(
            transit_result,
            preferences=preferences,
            weather=weather,
        )
        options = [
            self._driving_option(driving_result),
            self._walking_option(walking_result),
            transit_option,
        ]
        if arrival_deadline is not None and reference_at is not None:
            options = self._apply_arrival_deadline(
                options,
                arrival_deadline,
                reference_at,
            )
        recommendation = recommend_travel_mode(
            options,
            preferences,
            weather=weather,
        )
        recommendation_variants = recommend_travel_variants(
            options,
            preferences,
            weather=weather,
        )
        if isinstance(weather_result, Exception):
            recommendation = recommendation.model_copy(
                update={
                    "limitations": list(
                        dict.fromkeys(
                            [
                                *recommendation.limitations,
                                f"天气服务失败：{weather_result}",
                            ]
                        )
                    )
                }
            )

        extra_limitations: list[str] = []
        if departure_time is not None and reference_at is not None:
            if departure_time.relation_to(reference_at) == "future":
                extra_limitations.append(
                    "天气使用出发时段预报；路线数据仍为查询时结果，"
                    "不代表未来出发时的实时路况或班次"
                )
            if departure_time.precision == "time_period":
                extra_limitations.append(
                    "用户仅提供时间段，天气按"
                    f" {departure_time.departure_at:%H:%M} 附近的逐小时预报估算"
                )
        if arrival_deadline is not None:
            extra_limitations.extend(
                [
                    "天气按目标到达时段的逐小时预报估算",
                    "各方案最晚出发时间按查询时路线耗时和"
                    f" {arrival_deadline.buffer_minutes} 分钟缓冲反推；"
                    "不代表未来路况或班次",
                ]
            )
        if extra_limitations:
            recommendation = recommendation.model_copy(
                update={
                    "limitations": list(
                        dict.fromkeys(
                            [*recommendation.limitations, *extra_limitations]
                        )
                    )
                }
            )

        route_snapshot_at = reference_at or datetime.now(UTC)
        return TravelComparisonResult(
            context=JourneyContext(
                city=city,
                origin_name=origin_name,
                destination_name=destination_name,
                origin=origin,
                destination=destination,
                route_snapshot_at=route_snapshot_at,
                route_snapshot_expires_at=(
                    route_snapshot_at
                    + timedelta(seconds=route_snapshot_ttl_seconds)
                ),
                departure_time=departure_time,
                arrival_deadline=arrival_deadline,
                weather=weather,
                preferences=preferences,
            ),
            recommendation=recommendation,
            recommendation_variants=recommendation_variants,
            transit_candidates=(
                transit_result.options
                if isinstance(transit_result, TransitPlan)
                else []
            ),
            selected_transit_candidate_index=selected_transit_candidate_index,
        )

    @staticmethod
    def _apply_arrival_deadline(
        options: list[TravelOption],
        arrival_deadline: ArrivalDeadline,
        reference_at: datetime,
    ) -> list[TravelOption]:
        """Attach latest departures and reject schedules that already expired."""
        scheduled: list[TravelOption] = []
        for option in options:
            if option.status != "available" or option.duration_s is None:
                scheduled.append(option)
                continue
            latest_departure = arrival_deadline.latest_departure_at(
                option.duration_s
            )
            if latest_departure < reference_at.astimezone(
                latest_departure.tzinfo
            ):
                scheduled.append(
                    option.model_copy(
                        update={
                            "status": "unavailable",
                            "failure_reason": (
                                "按照预计耗时和"
                                f" {arrival_deadline.buffer_minutes} 分钟缓冲，"
                                "最晚出发时间已过"
                            ),
                        }
                    )
                )
                continue
            scheduled.append(
                option.model_copy(
                    update={"latest_departure_at": latest_departure}
                )
            )
        return scheduled

    @staticmethod
    def _driving_option(result: object) -> TravelOption:
        if not isinstance(result, RoutePlan):
            return TravelOption(
                mode="driving",
                status="failed",
                failure_reason=f"驾车路线服务失败：{result}",
            )

        traffic_counts = Counter(
            segment.status for segment in result.traffic_segments
        )
        return TravelOption(
            mode="driving",
            distance_m=result.distance_m,
            duration_s=result.duration_s,
            duration_basis=result.duration_basis,
            cost_yuan=None,
            tolls_yuan=result.tolls_yuan,
            taxi_cost_yuan=result.taxi_cost_yuan,
            transfer_count=0,
            traffic_status_counts=dict(traffic_counts),
            geometry=result.geometry,
            attribution=result.attribution,
        )

    @staticmethod
    def _walking_option(result: object) -> TravelOption:
        if not isinstance(result, RoutePlan):
            return TravelOption(
                mode="walking",
                status="failed",
                failure_reason=f"步行路线服务失败：{result}",
            )
        return TravelOption(
            mode="walking",
            distance_m=result.distance_m,
            duration_s=result.duration_s,
            duration_basis=result.duration_basis,
            walking_distance_m=result.distance_m,
            cost_yuan=0,
            transfer_count=0,
            geometry=result.geometry,
            attribution=result.attribution,
        )

    @staticmethod
    def _transit_option(
        result: object,
        *,
        preferences: TravelPreferences,
        weather: WeatherData | None,
    ) -> tuple[TravelOption, int | None]:
        if not isinstance(result, TransitPlan):
            return (
                TravelOption(
                    mode="transit",
                    status="failed",
                    failure_reason=f"公交路线服务失败：{result}",
                ),
                None,
            )
        return select_transit_candidate(
            result.options,
            attribution=result.attribution,
            preferences=preferences,
            weather=weather,
        )
