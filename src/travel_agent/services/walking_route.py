from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint, RoutePlan, RouteStep
from travel_agent.observability.http import observed_request
from travel_agent.services.route import RouteNotFoundError, RouteServiceError

WALKING_ROUTE_URL = (
    "https://api.openrouteservice.org/v2/directions/foot-walking/geojson"
)
WALKING_ROUTE_USER_AGENT = "smart-travel-agent/0.1 (learning project)"


class WalkingRouteService:
    """Plan and normalize walking routes from openrouteservice."""

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
        api_key = self._api_key or get_settings().ors_api_key.get_secret_value()
        if self._external_client is not None:
            return await self._fetch(
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
            return await self._fetch(client, api_key, origin, destination)

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        try:
            response = await observed_request(
                client,
                "POST",
                WALKING_ROUTE_URL,
                provider="openrouteservice",
                headers={
                    "Authorization": api_key,
                    "Content-Type": "application/json",
                    "User-Agent": WALKING_ROUTE_USER_AGENT,
                },
                json={
                    "coordinates": [
                        [origin.longitude, origin.latitude],
                        [destination.longitude, destination.latitude],
                    ],
                    "instructions": True,
                    "language": "zh-cn",
                },
            )
            response.raise_for_status()
            return self._parse_route(response.json(), origin, destination)
        except RouteNotFoundError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise RouteServiceError("步行路线服务暂时不可用，请稍后重试") from exc

    @staticmethod
    def _parse_route(
        payload: Any,
        origin: GeoPoint,
        destination: GeoPoint,
    ) -> RoutePlan:
        if not isinstance(payload, dict):
            raise TypeError("openrouteservice 响应必须是对象")

        features = payload.get("features")
        if not isinstance(features, list) or not features:
            raise RouteNotFoundError("未找到起点和终点之间的步行路线")

        feature = features[0]
        properties = feature["properties"]
        summary = properties["summary"]
        steps = [
            RouteStep(
                distance_m=step["distance"],
                duration_s=step["duration"],
                road_name=(
                    None
                    if step.get("name") in (None, "", "-")
                    else step["name"]
                ),
                maneuver_type=f"ors_instruction_{step.get('type', 'unknown')}",
                instruction=step.get("instruction") or None,
            )
            for segment in properties["segments"]
            for step in segment["steps"]
        ]
        geometry = [
            GeoPoint(latitude=coordinate[1], longitude=coordinate[0])
            for coordinate in feature["geometry"]["coordinates"]
        ]

        return RoutePlan(
            mode="walking",
            origin=origin,
            destination=destination,
            distance_m=summary["distance"],
            duration_s=summary["duration"],
            steps=steps,
            geometry=geometry,
            attribution=payload["metadata"]["attribution"],
        )
