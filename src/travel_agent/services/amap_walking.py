from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint, RoutePlan, RouteStep
from travel_agent.observability.http import observed_request
from travel_agent.services.amap_coordinate import (
    AMAP_USER_AGENT,
    AmapCoordinate,
    AmapCoordinateService,
    AmapCoordinateServiceError,
)
from travel_agent.services.route import RouteNotFoundError, RouteServiceError
from travel_agent.services.route_geometry import (
    compact_geometry,
    merge_geometry,
    parse_amap_polyline,
)

AMAP_WALKING_URL = "https://restapi.amap.com/v5/direction/walking"
AMAP_WALKING_ATTRIBUTION = "步行路线数据来源：高德地图 Web服务 API"


class AmapWalkingRouteService:
    """Plan a walking route with AMap Route Planning 2.0."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._external_client = client
        self._api_key = api_key

    async def plan_walking_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        api_key = self._api_key or get_settings().amap_api_key.get_secret_value()
        if self._external_client is not None:
            return await self._plan(
                self._external_client,
                api_key,
                origin,
                destination,
            )

        proxy_url = get_settings().route_proxy_url
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._plan(client, api_key, origin, destination)

    async def _plan(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        try:
            amap_points = await AmapCoordinateService(
                client,
                api_key=api_key,
            ).convert_wgs84([origin, destination])
            response = await observed_request(
                client,
                "GET",
                AMAP_WALKING_URL,
                provider="amap-walking",
                params={
                    "key": api_key,
                    "origin": self._format_coordinate(amap_points[0]),
                    "destination": self._format_coordinate(amap_points[1]),
                    "show_fields": "cost,navi,polyline",
                    "output": "json",
                },
                headers={"User-Agent": AMAP_USER_AGENT},
            )
            response.raise_for_status()
            return self._parse_route(response.json(), origin, destination)
        except RouteNotFoundError:
            raise
        except AmapCoordinateServiceError as exc:
            raise RouteServiceError(str(exc)) from exc
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RouteServiceError(
                "高德步行路线服务暂时不可用，请稍后重试"
            ) from exc

    @staticmethod
    def _format_coordinate(coordinate: AmapCoordinate) -> str:
        return f"{coordinate.longitude:.6f},{coordinate.latitude:.6f}"

    @classmethod
    def _parse_route(
        cls,
        payload: Any,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise RouteServiceError(
                "高德步行路线服务暂时不可用，请稍后重试"
            )

        route = payload.get("route")
        paths = route.get("paths") if isinstance(route, dict) else None
        if not isinstance(paths, list) or not paths:
            raise RouteNotFoundError("未找到起点和终点之间的步行路线")

        path = paths[0]
        cost = path["cost"]
        return RoutePlan(
            mode="walking",
            origin=origin,
            destination=destination,
            distance_m=path["distance"],
            duration_s=cost["duration"],
            duration_basis="static_without_live_traffic",
            steps=[cls._parse_step(step) for step in path.get("steps", [])],
            geometry=compact_geometry(
                merge_geometry(
                    parse_amap_polyline(step.get("polyline"))
                    for step in path.get("steps", [])
                )
            ),
            attribution=AMAP_WALKING_ATTRIBUTION,
        )

    @staticmethod
    def _parse_step(step: dict[str, Any]) -> RouteStep:
        cost = step.get("cost") or {}
        navi = step.get("navi") or {}
        return RouteStep(
            distance_m=step["step_distance"],
            duration_s=cost.get("duration", 0),
            road_name=step.get("road_name") or None,
            maneuver_type=navi.get("action") or "continue",
            instruction=step.get("instruction") or None,
        )
