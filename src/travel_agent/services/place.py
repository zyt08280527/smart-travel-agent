import asyncio
from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.place import (
    Place,
    PlaceSearchResult,
    RouteEndpointCandidates,
)
from travel_agent.observability.http import observed_request

PLACE_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
PLACE_SEARCH_USER_AGENT = "smart-travel-agent/0.1 (learning project)"
OSM_ATTRIBUTION = "Data © OpenStreetMap contributors, ODbL 1.0"
NOMINATIM_REQUEST_INTERVAL_SECONDS = 1.1


class PlaceServiceError(RuntimeError):
    """A user-safe error raised when place data cannot be obtained."""


class PlaceService:
    """Search and normalize places from OpenStreetMap Nominatim."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client

    async def search_places(
        self,
        query: str,
        limit: int = 5,
    ) -> PlaceSearchResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("地点搜索词不能为空")
        if not 1 <= limit <= 5:
            raise ValueError("地点结果数量必须在 1 到 5 之间")

        if self._external_client is not None:
            return await self._fetch(self._external_client, normalized_query, limit)

        proxy_url = get_settings().place_proxy_url
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._fetch(client, normalized_query, limit)

    async def resolve_route_endpoints(
        self,
        origin_query: str,
        destination_query: str,
        limit: int = 3,
    ) -> RouteEndpointCandidates:
        """Search two route endpoints sequentially at a provider-safe interval."""
        origin = await self.search_places(origin_query, limit)
        await asyncio.sleep(NOMINATIM_REQUEST_INTERVAL_SECONDS)
        destination = await self.search_places(destination_query, limit)

        attributions = dict.fromkeys(
            (origin.attribution, destination.attribution)
        )
        return RouteEndpointCandidates(
            origin=origin,
            destination=destination,
            attribution="; ".join(attributions),
        )

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        query: str,
        limit: int,
    ) -> PlaceSearchResult:
        try:
            response = await observed_request(
                client,
                "GET",
                PLACE_SEARCH_URL,
                provider="openstreetmap-nominatim",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": limit,
                },
                headers={
                    "User-Agent": PLACE_SEARCH_USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                },
            )
            response.raise_for_status()
            return self._parse_results(response.json(), query)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise PlaceServiceError("地点搜索服务暂时不可用，请稍后重试") from exc

    @staticmethod
    def _parse_results(payload: Any, query: str) -> PlaceSearchResult:
        if not isinstance(payload, list):
            raise TypeError("地点搜索响应必须是列表")

        places = [
            Place(
                display_name=item["display_name"],
                latitude=item["lat"],
                longitude=item["lon"],
                category=item.get("category") or item.get("class"),
                place_type=item.get("type"),
                importance=item.get("importance"),
            )
            for item in payload
        ]
        attribution = next(
            (
                item["licence"]
                for item in payload
                if isinstance(item.get("licence"), str) and item["licence"]
            ),
            OSM_ATTRIBUTION,
        )
        return PlaceSearchResult(
            query=query,
            places=places,
            attribution=attribution,
        )
