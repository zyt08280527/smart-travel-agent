from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from travel_agent.services.place import (
    NOMINATIM_REQUEST_INTERVAL_SECONDS,
    PLACE_SEARCH_URL,
    PLACE_SEARCH_USER_AGENT,
    PlaceService,
    PlaceServiceError,
)


@pytest.mark.asyncio
@respx.mock
async def test_search_places_returns_normalized_domain_model() -> None:
    route = respx.get(PLACE_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "display_name": "深圳大学，南山区，深圳市，广东省，中国",
                    "lat": "22.5333",
                    "lon": "113.9304",
                    "category": "amenity",
                    "type": "university",
                    "importance": 0.6,
                    "licence": "Data © OpenStreetMap contributors",
                }
            ],
        )
    )

    async with httpx.AsyncClient() as client:
        result = await PlaceService(client).search_places(" 深圳大学 ", limit=3)

    assert result.query == "深圳大学"
    assert result.places[0].latitude == 22.5333
    assert result.places[0].category == "amenity"
    assert result.places[0].place_type == "university"
    assert route.calls.last.request.headers["User-Agent"] == PLACE_SEARCH_USER_AGENT


@pytest.mark.asyncio
@respx.mock
async def test_search_places_returns_empty_candidates() -> None:
    respx.get(PLACE_SEARCH_URL).mock(return_value=httpx.Response(200, json=[]))

    async with httpx.AsyncClient() as client:
        result = await PlaceService(client).search_places("不存在的地点")

    assert result.places == []
    assert result.attribution


@pytest.mark.asyncio
@respx.mock
async def test_resolve_route_endpoints_searches_sequentially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(PLACE_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=[
                    {
                        "display_name": "起点",
                        "lat": "22.5",
                        "lon": "113.9",
                        "licence": "Data © OpenStreetMap contributors",
                    }
                ],
            ),
            httpx.Response(
                200,
                json=[
                    {
                        "display_name": "终点",
                        "lat": "22.6",
                        "lon": "114.0",
                        "licence": "Data © OpenStreetMap contributors",
                    }
                ],
            ),
        ]
    )
    sleep_mock = AsyncMock()
    monkeypatch.setattr("travel_agent.services.place.asyncio.sleep", sleep_mock)

    async with httpx.AsyncClient() as client:
        result = await PlaceService(client).resolve_route_endpoints(
            "起点",
            "终点",
            limit=1,
        )

    assert result.origin.places[0].display_name == "起点"
    assert result.destination.places[0].display_name == "终点"
    assert len(route.calls) == 2
    sleep_mock.assert_awaited_once_with(NOMINATIM_REQUEST_INTERVAL_SECONDS)


@pytest.mark.asyncio
@respx.mock
async def test_search_places_hides_upstream_failure() -> None:
    respx.get(PLACE_SEARCH_URL).mock(return_value=httpx.Response(503))

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
