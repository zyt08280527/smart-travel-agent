from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint, RoutePlan, RouteStep
from travel_agent.observability.http import observed_request

ROUTE_BASE_URL = "https://router.project-osrm.org/route/v1/driving"
ROUTE_USER_AGENT = "smart-travel-agent/0.1 (learning project)"
ROUTE_ATTRIBUTION = "Routing data © OpenStreetMap contributors"


class RouteServiceError(RuntimeError):
    """A user-safe error raised when route data cannot be obtained."""


class RouteNotFoundError(RouteServiceError):
    """Raised when the provider cannot find a route between two points."""


class RouteService:
    """Plan and normalize driving routes from OSRM."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client

    async def plan_driving_route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        if self._external_client is not None:
            return await self._fetch(self._external_client, origin, destination)

        proxy_url = get_settings().route_proxy_url
        timeout = httpx.Timeout(15.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._fetch(client, origin, destination)

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        coordinates = (
            f"{origin.longitude},{origin.latitude};"
            f"{destination.longitude},{destination.latitude}"
        )
        url = f"{ROUTE_BASE_URL}/{coordinates}"

        try:
            response = await observed_request(
                client,
                "GET",
                url,
                provider="osrm",
                observed_path="/route/v1/driving/{coordinates}",
                params={
                    "steps": "true",
                    "geometries": "geojson",
                    "overview": "full",
                },
                headers={"User-Agent": ROUTE_USER_AGENT},
            )
            response.raise_for_status()
            return self._parse_route(response.json(), origin, destination)
        except RouteNotFoundError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RouteServiceError("路线规划服务暂时不可用，请稍后重试") from exc

    @staticmethod
    def _parse_route(
        payload: Any,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        if not isinstance(payload, dict):
            raise TypeError("OSRM 响应必须是对象")
        if payload.get("code") == "NoRoute":
            raise RouteNotFoundError("未找到起点和终点之间的驾车路线")
        if payload.get("code") != "Ok":
            raise ValueError("OSRM 未返回成功状态")

        route = payload["routes"][0]
        steps = [
            RouteStep(
                distance_m=step["distance"],
                duration_s=step["duration"],
                road_name=step.get("name") or None,
                maneuver_type=step["maneuver"]["type"],
                maneuver_modifier=step["maneuver"].get("modifier"),
            )
            for leg in route["legs"]
            for step in leg["steps"]
        ]
        geometry = [
            GeoPoint(latitude=coordinate[1], longitude=coordinate[0])
            for coordinate in route["geometry"]["coordinates"]
        ]

        return RoutePlan(
            origin=origin,
            destination=destination,
            distance_m=route["distance"],
            duration_s=route["duration"],
            steps=steps,
            geometry=geometry,
            attribution=ROUTE_ATTRIBUTION,
        )
