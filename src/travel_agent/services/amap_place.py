from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.place import Place, PlaceSearchResult
from travel_agent.observability.http import observed_request
from travel_agent.services.amap_coordinate import AMAP_USER_AGENT
from travel_agent.services.coordinate_system import gcj02_to_wgs84

AMAP_PLACE_SEARCH_URL = "https://restapi.amap.com/v5/place/text"
AMAP_PLACE_ATTRIBUTION = "地点数据来源：高德地图 Web服务 API"


class AmapPlaceServiceError(RuntimeError):
    """A user-safe error raised when AMap place search fails."""


class AmapPlaceService:
    """Search AMap POIs and normalize their GCJ-02 coordinates as WGS84."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._external_client = client
        self._api_key = api_key

    async def search_places(self, query: str, limit: int) -> PlaceSearchResult:
        api_key = self._api_key or get_settings().amap_api_key.get_secret_value()
        if self._external_client is not None:
            return await self._fetch(self._external_client, api_key, query, limit)

        proxy_url = get_settings().route_proxy_url
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._fetch(client, api_key, query, limit)

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        query: str,
        limit: int,
    ) -> PlaceSearchResult:
        try:
            response = await observed_request(
                client,
                "GET",
                AMAP_PLACE_SEARCH_URL,
                provider="amap-place",
                params={
                    "key": api_key,
                    "keywords": query,
                    "page_size": limit,
                    "page_num": 1,
                },
                headers={"User-Agent": AMAP_USER_AGENT},
            )
            response.raise_for_status()
            return self._parse_results(response.json(), query)
        except AmapPlaceServiceError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AmapPlaceServiceError(
                "备用地点搜索服务暂时不可用"
            ) from exc

    @staticmethod
    def _parse_results(payload: Any, query: str) -> PlaceSearchResult:
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise AmapPlaceServiceError("备用地点搜索服务暂时不可用")
        pois = payload.get("pois")
        if not isinstance(pois, list):
            raise AmapPlaceServiceError("备用地点搜索响应格式错误")

        places: list[Place] = []
        for rank, poi in enumerate(pois):
            if not isinstance(poi, dict):
                continue
            location = poi.get("location")
            if not isinstance(location, str) or "," not in location:
                continue
            longitude_text, latitude_text = location.split(",", maxsplit=1)
            wgs84 = gcj02_to_wgs84(
                latitude=float(latitude_text),
                longitude=float(longitude_text),
            )
            name = str(poi.get("name") or query)
            address = poi.get("address")
            display_name = (
                f"{name}，{address}"
                if isinstance(address, str) and address
                else name
            )
            places.append(
                Place(
                    display_name=display_name,
                    latitude=wgs84.latitude,
                    longitude=wgs84.longitude,
                    category=(str(poi.get("type")) if poi.get("type") else None),
                    place_type=(
                        str(poi.get("typecode"))
                        if poi.get("typecode")
                        else None
                    ),
                    importance=1.0 / (rank + 1),
                )
            )
        return PlaceSearchResult(
            query=query,
            places=places,
            attribution=AMAP_PLACE_ATTRIBUTION,
            provider="amap",
        )
