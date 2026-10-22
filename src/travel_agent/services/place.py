import httpx

from travel_agent.domain.place import PlaceSearchResult, RouteEndpointCandidates
from travel_agent.services.amap_place import (
    AmapPlaceService,
    AmapPlaceServiceError,
)


class PlaceServiceError(RuntimeError):
    """A user-safe error raised when place data cannot be obtained."""


class PlaceService:
    """Validate place queries and resolve candidates through AMap POI search."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        amap_service: AmapPlaceService | None = None,
    ) -> None:
        self._amap_service = amap_service or AmapPlaceService(client)

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

        try:
            return await self._amap_service.search_places(normalized_query, limit)
        except AmapPlaceServiceError as exc:
            raise PlaceServiceError("地点搜索服务暂时不可用，请稍后重试") from exc

    async def resolve_route_endpoints(
        self,
        origin_query: str,
        destination_query: str,
        limit: int = 3,
    ) -> RouteEndpointCandidates:
        """Resolve both endpoints sequentially with the same search provider."""
        origin = await self.search_places(origin_query, limit)
        destination = await self.search_places(destination_query, limit)
        return RouteEndpointCandidates(
            origin=origin,
            destination=destination,
            attribution=origin.attribution,
        )
