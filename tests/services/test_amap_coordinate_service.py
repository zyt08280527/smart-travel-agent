import httpx
import pytest
import respx

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_coordinate import (
    AMAP_COORDINATE_CONVERT_URL,
    AmapCoordinateService,
    AmapCoordinateServiceError,
)

ORIGIN = GeoPoint(latitude=22.5359023, longitude=113.9314749)
DESTINATION = GeoPoint(latitude=22.6009872, longitude=113.987959)


@pytest.mark.asyncio
@respx.mock
async def test_convert_wgs84_returns_gcj02_points_in_original_order() -> None:
    route = respx.get(AMAP_COORDINATE_CONVERT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "infocode": "10000",
                "locations": "113.936580,22.533180;113.993070,22.598250",
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await AmapCoordinateService(
            client,
            api_key="amap-test-key",
        ).convert_wgs84([ORIGIN, DESTINATION])

    assert len(result) == 2
    assert result[0].coordinate_system == "gcj02"
    assert result[0].longitude == 113.93658
    assert result[1].latitude == 22.59825

    request = route.calls.last.request
    assert request.url.params["key"] == "amap-test-key"
    assert request.url.params["coordsys"] == "gps"
    assert request.url.params["locations"] == (
        "113.931475,22.535902|113.987959,22.600987"
    )


@pytest.mark.asyncio
@respx.mock
async def test_convert_wgs84_hides_provider_error() -> None:
    respx.get(AMAP_COORDINATE_CONVERT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "0",
                "info": "INVALID_USER_KEY",
                "infocode": "10001",
            },
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(
            AmapCoordinateServiceError,
            match="坐标转换服务暂时不可用",
        ):
            await AmapCoordinateService(
                client,
                api_key="invalid-test-key",
            ).convert_wgs84([ORIGIN])


@pytest.mark.asyncio
@respx.mock
async def test_convert_wgs84_rejects_mismatched_result_count() -> None:
    respx.get(AMAP_COORDINATE_CONVERT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "locations": "113.936580,22.533180",
            },
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(
            AmapCoordinateServiceError,
            match="结果数量不一致",
        ):
            await AmapCoordinateService(
                client,
                api_key="amap-test-key",
            ).convert_wgs84([ORIGIN, DESTINATION])


@pytest.mark.asyncio
async def test_convert_wgs84_requires_at_least_one_point() -> None:
    with pytest.raises(ValueError, match="至少需要一个"):
        await AmapCoordinateService(api_key="amap-test-key").convert_wgs84([])
