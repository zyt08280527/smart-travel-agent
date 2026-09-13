from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from travel_agent.domain.journey_time import DepartureTime
from travel_agent.domain.weather import ForecastWeather
from travel_agent.observability.http import capture_external_http_requests
from travel_agent.services.weather import (
    FORECAST_URL,
    GEOCODING_URL,
    CityNotFoundError,
    WeatherService,
    WeatherServiceError,
    WeatherTimeUnsupportedError,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
REFERENCE_TIME = datetime(2026, 9, 11, 10, 0, tzinfo=SHANGHAI)


def build_departure(offset: timedelta) -> DepartureTime:
    return DepartureTime(
        departure_at=REFERENCE_TIME + offset,
        timezone="Asia/Shanghai",
        precision="exact",
        source_text="明天下午三点",
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
async def test_get_forecast_weather_retries_without_city_suffix() -> None:
    geocoding_route = respx.get(GEOCODING_URL).mock(
        side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(
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
            ),
        ]
    )
    respx.get(FORECAST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-12T15:00"],
                    "temperature_2m": [30],
                    "apparent_temperature": [33],
                    "precipitation": [0],
                    "weather_code": [2],
                    "wind_speed_10m": [8],
                }
            },
        )
    )
    departure = DepartureTime(
        departure_at=datetime(2026, 9, 12, 15, 0, tzinfo=SHANGHAI),
        timezone="Asia/Shanghai",
        precision="exact",
        source_text="明天下午三点",
    )

    async with httpx.AsyncClient() as client:
        result = await WeatherService(client).get_forecast_weather(
            "深圳市",
            departure,
        )

    assert result.location.name == "深圳"
    assert [call.request.url.params["name"] for call in geocoding_route.calls] == [
        "深圳市",
        "深圳",
    ]


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


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_for_future_departure_returns_nearest_hour() -> None:
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
    forecast_route = respx.get(FORECAST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "hourly": {
                    "time": [
                        "2026-09-12T14:00",
                        "2026-09-12T15:00",
                        "2026-09-12T16:00",
                    ],
                    "temperature_2m": [29, 30, 29.5],
                    "apparent_temperature": [31, 33, 32],
                    "precipitation": [0, 2.5, 1],
                    "weather_code": [2, 61, 3],
                    "wind_speed_10m": [8, 12, 10],
                }
            },
        )
    )
    departure = DepartureTime(
        departure_at=datetime(2026, 9, 12, 15, 20, tzinfo=SHANGHAI),
        timezone="Asia/Shanghai",
        precision="exact",
        source_text="明天下午3点20分",
    )

    async with httpx.AsyncClient() as client:
        result = await WeatherService(client).get_weather_for_departure(
            "深圳",
            departure,
            reference_at=REFERENCE_TIME,
        )

    assert isinstance(result, ForecastWeather)
    assert result.forecast_at == "2026-09-12T15:00"
    assert result.temperature_c == 30
    assert result.apparent_temperature_c == 33
    assert result.precipitation_mm == 2.5
    assert result.condition == "小雨"
    assert forecast_route.calls[0].request.url.params["timezone"] == "Asia/Shanghai"
    assert forecast_route.calls[0].request.url.params["start_date"] == "2026-09-12"


@pytest.mark.asyncio
async def test_get_weather_for_departure_rejects_past_time() -> None:
    with pytest.raises(WeatherTimeUnsupportedError, match="已经过去"):
        await WeatherService().get_weather_for_departure(
            "深圳",
            build_departure(timedelta(hours=-1)),
            reference_at=REFERENCE_TIME,
        )


@pytest.mark.asyncio
async def test_get_weather_for_departure_rejects_out_of_range_time() -> None:
    with pytest.raises(WeatherTimeUnsupportedError, match="16天"):
        await WeatherService().get_weather_for_departure(
            "深圳",
            build_departure(timedelta(days=16)),
            reference_at=REFERENCE_TIME,
        )
