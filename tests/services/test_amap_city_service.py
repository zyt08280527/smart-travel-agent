import httpx
import pytest
import respx

from travel_agent.services.amap_city import (
    AMAP_REVERSE_GEOCODE_URL,
    AmapCityService,
    AmapCityServiceError,
)
from travel_agent.services.amap_coordinate import AmapCoordinate

SHENZHEN_POINT = AmapCoordinate(
    latitude=22.532868,
    longitude=113.936340,
)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_city_returns_normalized_city_metadata() -> None:
    route = respx.get(AMAP_REVERSE_GEOCODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "regeocode": {
                    "formatted_address": "广东省深圳市南山区",
                    "addressComponent": {
                        "country": "中国",
                        "province": "广东省",
                        "city": "深圳市",
                        "citycode": "0755",
                        "district": "南山区",
                        "adcode": "440305",
                    },
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await AmapCityService(
            client,
            api_key="amap-test-key",
        ).resolve_city(SHENZHEN_POINT)

    assert result.city_name == "深圳市"
    assert result.city_code == "0755"
    assert result.adcode == "440305"
    assert result.coordinate == SHENZHEN_POINT

    request = route.calls.last.request
    assert request.url.params["key"] == "amap-test-key"
    assert request.url.params["location"] == "113.936340,22.532868"
    assert request.url.params["extensions"] == "base"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_city_uses_province_name_for_direct_municipality() -> None:
    respx.get(AMAP_REVERSE_GEOCODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "regeocode": {
                    "addressComponent": {
                        "province": "北京市",
                        "city": [],
                        "citycode": "010",
                        "district": "海淀区",
                        "adcode": "110108",
                    }
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await AmapCityService(
            client,
            api_key="amap-test-key",
        ).resolve_city(SHENZHEN_POINT)

    assert result.city_name == "北京市"
    assert result.city_code == "010"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_city_hides_provider_error() -> None:
    respx.get(AMAP_REVERSE_GEOCODE_URL).mock(
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
            AmapCityServiceError,
            match="城市解析服务暂时不可用",
        ):
            await AmapCityService(
                client,
                api_key="invalid-test-key",
            ).resolve_city(SHENZHEN_POINT)


@pytest.mark.asyncio
@respx.mock
async def test_resolve_city_rejects_missing_city_code() -> None:
    respx.get(AMAP_REVERSE_GEOCODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "regeocode": {
                    "addressComponent": {
                        "province": "广东省",
                        "city": "深圳市",
                        "district": "南山区",
                        "adcode": "440305",
                    }
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(
            AmapCityServiceError,
            match="城市解析服务暂时不可用",
        ):
            await AmapCityService(
                client,
                api_key="amap-test-key",
            ).resolve_city(SHENZHEN_POINT)
