import httpx
import pytest
import respx

from travel_agent.observability.http import capture_external_http_requests
from travel_agent.services.weather import (
    FORECAST_URL,
    GEOCODING_URL,
    CityNotFoundError,
    WeatherService,
    WeatherServiceError,
)


@pytest.mark.asyncio
@respx.mock
async def test_get_current_weather_returns_normalized_domain_model() -> None:
    respx.get(GEOCODING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "深圳",
                        "country": "中国",
                        "admin1": "广东",
                        "latitude": 22.5455,
                        "longitude": 114.0683,
                    }
                ]
            },
        )
    )
    respx.get(FORECAST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-07-21T16:00",
                    "temperature_2m": 31.2,
                    "apparent_temperature": 36.1,
                    "precipitation": 0.0,
                    "weather_code": 2,
                    "wind_speed_10m": 9.8,
                }
            },
        )
    )

    async with httpx.AsyncClient() as client:
        with capture_external_http_requests() as observations:
            result = await WeatherService(client).get_current_weather(" 深圳 ")

    assert result.location.name == "深圳"
    assert result.temperature_c == 31.2
    assert result.apparent_temperature_c == 36.1
    assert result.condition == "局部多云"
    assert [item.provider for item in observations] == [
        "open-meteo-geocoding",
        "open-meteo-weather",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_get_current_weather_rejects_unknown_city() -> None:
    respx.get(GEOCODING_URL).mock(return_value=httpx.Response(200, json={"results": []}))

    async with httpx.AsyncClient() as client:
        with pytest.raises(CityNotFoundError, match="未找到城市"):
            await WeatherService(client).get_current_weather("不存在的城市")


@pytest.mark.asyncio
@respx.mock
async def test_get_current_weather_hides_upstream_failure() -> None:
    respx.get(GEOCODING_URL).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(WeatherServiceError, match="天气服务暂时不可用"):
            await WeatherService(client).get_current_weather("深圳")


@pytest.mark.asyncio
async def test_get_current_weather_rejects_blank_city() -> None:
    with pytest.raises(ValueError, match="城市名称不能为空"):
        await WeatherService().get_current_weather("   ")
