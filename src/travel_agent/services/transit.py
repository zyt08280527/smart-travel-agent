from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint
from travel_agent.domain.transit import TransitLeg, TransitOption, TransitPlan
from travel_agent.observability.http import observed_request
from travel_agent.services.amap_city import (
    AmapCityService,
    AmapCityServiceError,
)
from travel_agent.services.amap_coordinate import (
    AMAP_USER_AGENT,
    AmapCoordinate,
    AmapCoordinateService,
    AmapCoordinateServiceError,
)
from travel_agent.services.route_geometry import (
    compact_geometry,
    merge_geometry,
    parse_amap_polyline,
)

AMAP_TRANSIT_URL = "https://restapi.amap.com/v5/direction/transit/integrated"
AMAP_TRANSIT_ATTRIBUTION = "公交路线数据来源：高德地图 Web服务 API"
SUPPORTED_TRANSIT_STRATEGIES = frozenset(range(9))


class TransitServiceError(RuntimeError):
    """A user-safe error raised when transit data cannot be obtained."""


class TransitNotFoundError(TransitServiceError):
    """Raised when no public-transport route exists for the endpoints."""


class TransitService:
    """Plan and normalize public-transport routes from AMap."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._external_client = client
        self._api_key = api_key

    async def plan_transit_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        strategy: int = 0,
        alternative_routes: int = 3,
    ) -> TransitPlan:
        """Plan transit after coordinate conversion and city resolution."""
        if strategy not in SUPPORTED_TRANSIT_STRATEGIES:
            raise ValueError("公交换乘策略必须在 0 到 8 之间")
        if not 1 <= alternative_routes <= 10:
            raise ValueError("公交候选方案数量必须在 1 到 10 之间")

        api_key = self._api_key or get_settings().amap_api_key.get_secret_value()
        if self._external_client is not None:
            return await self._plan(
                self._external_client,
                api_key,
                origin,
                destination,
                strategy,
                alternative_routes,
            )

        proxy_url = get_settings().route_proxy_url
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._plan(
                client,
                api_key,
                origin,
                destination,
                strategy,
                alternative_routes,
            )

    async def _plan(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        origin: GeoPoint,
        destination: GeoPoint,
        strategy: int,
        alternative_routes: int,
    ) -> TransitPlan:
        try:
            amap_points = await AmapCoordinateService(
                client,
                api_key=api_key,
            ).convert_wgs84([origin, destination])
            city_service = AmapCityService(client, api_key=api_key)
            origin_city = await city_service.resolve_city(amap_points[0])
            destination_city = await city_service.resolve_city(amap_points[1])

            response = await observed_request(
                client,
                "GET",
                AMAP_TRANSIT_URL,
                provider="amap-transit",
                params={
                    "key": api_key,
                    "origin": self._format_coordinate(amap_points[0]),
                    "destination": self._format_coordinate(amap_points[1]),
                    "city1": origin_city.city_code,
                    "city2": destination_city.city_code,
                    "strategy": strategy,
                    "AlternativeRoute": alternative_routes,
                    "show_fields": "cost,polyline",
                    "output": "json",
                },
                headers={"User-Agent": AMAP_USER_AGENT},
            )
            response.raise_for_status()
            return self._parse_plan(
                response.json(),
                origin,
                destination,
                origin_city.city_code,
                destination_city.city_code,
                strategy,
            )
        except TransitNotFoundError:
            raise
        except (AmapCoordinateServiceError, AmapCityServiceError) as exc:
            raise TransitServiceError(str(exc)) from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise TransitServiceError(
                "公交路线服务暂时不可用，请稍后重试"
            ) from exc

    @staticmethod
    def _format_coordinate(coordinate: AmapCoordinate) -> str:
        return f"{coordinate.longitude:.6f},{coordinate.latitude:.6f}"

    @classmethod
    def _parse_plan(
        cls,
        payload: Any,
        origin: GeoPoint,
        destination: GeoPoint,
        origin_city_code: str,
        destination_city_code: str,
        strategy: int,
    ) -> TransitPlan:
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise TransitServiceError(
                "公交路线服务暂时不可用，请稍后重试"
            )

        transits = payload["route"]["transits"]
        if not isinstance(transits, list) or not transits:
            raise TransitNotFoundError("未找到起点和终点之间的公交路线")

        options = [cls._parse_option(transit) for transit in transits]
        return TransitPlan(
            origin=origin,
            destination=destination,
            origin_city_code=origin_city_code,
            destination_city_code=destination_city_code,
            strategy=strategy,
            options=options,
            attribution=AMAP_TRANSIT_ATTRIBUTION,
        )

    @classmethod
    def _parse_option(cls, transit: dict[str, Any]) -> TransitOption:
        legs: list[TransitLeg] = []
        segments = transit["segments"]
        for segment in segments:
            walking_leg = cls._parse_walking_leg(segment.get("walking"))
            if walking_leg is not None:
                legs.append(walking_leg)

            vehicle_leg = cls._parse_vehicle_leg(segment.get("bus"))
            if vehicle_leg is not None:
                legs.append(vehicle_leg)

        ride_count = sum(
            leg.mode in {"bus", "subway", "railway"} for leg in legs
        )
        return TransitOption(
            distance_m=transit["distance"],
            duration_s=transit["cost"]["duration"],
            walking_distance_m=transit["walking_distance"],
            cost_yuan=cls._optional_float(
                transit["cost"].get("transit_fee")
            ),
            night_service=transit.get("nightflag") == "1",
            transfer_count=max(ride_count - 1, 0),
            legs=legs,
            geometry=compact_geometry(
                merge_geometry(leg.geometry for leg in legs)
            ),
        )

    @staticmethod
    def _parse_walking_leg(walking: Any) -> TransitLeg | None:
        if not isinstance(walking, dict) or not walking:
            return None
        distance = float(walking.get("distance") or 0)
        if distance <= 0:
            return None

        steps = walking.get("steps")
        instructions = [
            step["instruction"]
            for step in steps
            if isinstance(step, dict)
            and isinstance(step.get("instruction"), str)
            and step["instruction"]
        ] if isinstance(steps, list) else []
        return TransitLeg(
            mode="walking",
            distance_m=distance,
            duration_s=TransitService._optional_float(
                walking.get("cost", {}).get("duration")
            ),
            instruction="；".join(instructions) or None,
            geometry=compact_geometry(
                merge_geometry(
                    parse_amap_polyline(step.get("polyline"))
                    for step in steps
                    if isinstance(step, dict)
                )
            ),
        )

    @staticmethod
    def _parse_vehicle_leg(bus: Any) -> TransitLeg | None:
        if not isinstance(bus, dict):
            return None
        buslines = bus.get("buslines")
        if not isinstance(buslines, list) or not buslines:
            return None

        line = buslines[0]
        line_type = str(line.get("type") or "")
        mode = "subway" if "地铁" in line_type else "bus"
        departure_stop = line.get("departure_stop") or {}
        arrival_stop = line.get("arrival_stop") or {}
        return TransitLeg(
            mode=mode,
            distance_m=line["distance"],
            duration_s=TransitService._optional_float(
                line.get("cost", {}).get("duration")
            ),
            line_name=line.get("name") or None,
            departure_stop=departure_stop.get("name") or None,
            arrival_stop=arrival_stop.get("name") or None,
            via_stop_count=(
                int(line["via_num"])
                if str(line.get("via_num") or "").isdigit()
                else None
            ),
            geometry=compact_geometry(
                parse_amap_polyline(line.get("polyline"))
            ),
        )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        return float(value)
