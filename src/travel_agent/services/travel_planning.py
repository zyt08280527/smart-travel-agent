import asyncio
from collections import Counter
from datetime import datetime

from travel_agent.domain.decision import (
    JourneyContext,
    TravelComparisonResult,
    TravelOption,
    TravelPreferences,
)
from travel_agent.domain.journey_time import DepartureTime
from travel_agent.domain.route import GeoPoint, RoutePlan
from travel_agent.domain.transit import TransitPlan
from travel_agent.domain.weather import WeatherData
from travel_agent.services.amap_driving import AmapDrivingRouteService
from travel_agent.services.amap_walking import AmapWalkingRouteService
from travel_agent.services.transit import TransitService
from travel_agent.services.travel_decision import (
    recommend_travel_mode,
    recommend_travel_variants,
)
from travel_agent.services.weather import WeatherService


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
        reference_at: datetime | None = None,
    ) -> TravelComparisonResult:
        """Run four independent provider calls without serial wait time."""
        preferences = preferences or TravelPreferences()
        if departure_time is not None and reference_at is None:
            raise ValueError("提供出发时间时必须同时提供当前参考时间")
        weather_call = (
            self._weather_service.get_current_weather(city)
            if departure_time is None
            else self._weather_service.get_weather_for_departure(
                city,
                departure_time,
                reference_at=reference_at,
            )
        )
        weather_result, driving_result, walking_result, transit_result = (
            await asyncio.gather(
                weather_call,
                self._driving_service.plan_driving_route(origin, destination),
                self._walking_service.plan_walking_route(origin, destination),
                self._transit_service.plan_transit_route(origin, destination),
                return_exceptions=True,
            )
        )

        weather = (
            weather_result
            if isinstance(weather_result, WeatherData)
            else None
        )
        options = [
            self._driving_option(driving_result),
            self._walking_option(walking_result),
            self._transit_option(transit_result),
        ]
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

        return TravelComparisonResult(
            context=JourneyContext(
                city=city,
                origin_name=origin_name,
                destination_name=destination_name,
                departure_time=departure_time,
                weather=weather,
                preferences=preferences,
            ),
            recommendation=recommendation,
            recommendation_variants=recommendation_variants,
        )

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
            attribution=result.attribution,
        )

    @staticmethod
    def _transit_option(result: object) -> TravelOption:
        if not isinstance(result, TransitPlan):
            return TravelOption(
                mode="transit",
                status="failed",
                failure_reason=f"公交路线服务失败：{result}",
            )
        option = result.options[0]
        return TravelOption(
            mode="transit",
            distance_m=option.distance_m,
            duration_s=option.duration_s,
            walking_distance_m=option.walking_distance_m,
            cost_yuan=option.cost_yuan,
            transfer_count=option.transfer_count,
            attribution=result.attribution,
        )
