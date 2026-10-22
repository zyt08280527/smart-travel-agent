import httpx
import pytest
import respx

from travel_agent.services.amap_place import (
    AMAP_PLACE_ATTRIBUTION,
    AMAP_PLACE_SEARCH_URL,
)
from travel_agent.services.place import PlaceService, PlaceServiceError


def amap_response(name: str, location: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "1",
            "pois": [
                {
                    "name": name,
                    "address": "南海大道3688号",
                    "location": location,
                    "type": "科教文化服务;学校;高等院校",
                    "typecode": "141201",
                }
            ],
        },
    )


@pytest.mark.asyncio
@respx.mock
async def test_search_places_uses_amap_and_returns_normalized_wgs84() -> None:
    route = respx.get(AMAP_PLACE_SEARCH_URL).mock(
        return_value=amap_response("深圳大学粤海校区", "113.940000,22.540000")
    )

    async with httpx.AsyncClient() as client:
        result = await PlaceService(client).search_places(" 深圳大学粤海校区 ", limit=3)

    place = result.places[0]
    assert result.query == "深圳大学粤海校区"
    assert result.provider == "amap"
    assert result.attribution == AMAP_PLACE_ATTRIBUTION
    assert place.display_name == "深圳大学粤海校区，南海大道3688号"
    assert place.category == "科教文化服务;学校;高等院校"
    assert place.place_type == "141201"
    assert place.latitude != 22.54
    assert place.longitude != 113.94
    assert route.call_count == 1
    assert route.calls.last.request.url.params["keywords"] == "深圳大学粤海校区"
    assert route.calls.last.request.url.params["page_size"] == "3"


@pytest.mark.asyncio
@respx.mock
async def test_search_places_returns_empty_candidates_without_fallback() -> None:
    route = respx.get(AMAP_PLACE_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"status": "1", "pois": []})
    )

    async with httpx.AsyncClient() as client:
        result = await PlaceService(client).search_places("不存在的地点")

    assert result.places == []
    assert result.provider == "amap"
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_resolve_route_endpoints_searches_amap_sequentially() -> None:
    route = respx.get(AMAP_PLACE_SEARCH_URL).mock(
        side_effect=[
            amap_response("起点", "113.94,22.54"),
            amap_response("终点", "113.99,22.60"),
        ]
    )

    async with httpx.AsyncClient() as client:
        result = await PlaceService(client).resolve_route_endpoints(
            " 起点 ", " 终点 ", limit=1
        )

    assert result.origin.provider == "amap"
    assert result.destination.provider == "amap"
    assert result.origin.places[0].display_name.startswith("起点")
    assert result.destination.places[0].display_name.startswith("终点")
    assert result.attribution == AMAP_PLACE_ATTRIBUTION
    assert [call.request.url.params["keywords"] for call in route.calls] == [
        "起点",
        "终点",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_search_places_hides_amap_http_failure() -> None:
    respx.get(AMAP_PLACE_SEARCH_URL).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(PlaceServiceError, match="地点搜索服务暂时不可用"):
            await PlaceService(client).search_places("深圳大学")


@pytest.mark.asyncio
@respx.mock
async def test_search_places_hides_amap_business_failure() -> None:
    respx.get(AMAP_PLACE_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"status": "0", "info": "DAILY_QUERY_OVER_LIMIT"}
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(PlaceServiceError, match="地点搜索服务暂时不可用"):
            await PlaceService(client).search_places("深圳大学")


@pytest.mark.asyncio
async def test_search_places_rejects_blank_query() -> None:
    with pytest.raises(ValueError, match="地点搜索词不能为空"):
        await PlaceService().search_places("   ")


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 6])
async def test_search_places_rejects_limit_outside_project_boundary(limit: int) -> None:
    with pytest.raises(ValueError, match="必须在 1 到 5 之间"):
        await PlaceService().search_places("深圳大学", limit=limit)
