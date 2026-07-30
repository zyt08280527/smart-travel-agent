from typing import Any

import httpx

from travel_agent.domain.weather import CurrentWeather, Location
from travel_agent.observability.http import observed_request

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_WEATHER_CONDITIONS = {
    0: "晴朗",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴天",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "轻度冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "轻度冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "伴小冰雹的雷暴",
    99: "伴大冰雹的雷暴",
}


def weather_code_to_condition(code: int) -> str:
    """Convert an Open-Meteo WMO weather code to a Chinese description."""
    return WMO_WEATHER_CONDITIONS.get(code, f"未知天气（WMO代码：{code}）")


class WeatherServiceError(RuntimeError):
    """A user-safe error raised when weather data cannot be obtained."""


class CityNotFoundError(WeatherServiceError):
    """Raised when the provider cannot resolve a city name."""


class WeatherService:
    """Resolve cities and fetch current weather from Open-Meteo."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client

    async def get_current_weather(self, city: str) -> CurrentWeather:
        normalized_city = city.strip()
        if not normalized_city:
            raise ValueError("城市名称不能为空")

        if self._external_client is not None:
            return await self._fetch(self._external_client, normalized_city)

        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await self._fetch(client, normalized_city)

    async def _fetch(self, client: httpx.AsyncClient, city: str) -> CurrentWeather:
        try:
            geocoding_response = await observed_request(
                client,
                "GET",
                GEOCODING_URL,
                provider="open-meteo-geocoding",
                params={"name": city, "count": 1, "language": "zh", "format": "json"},
            )
            geocoding_response.raise_for_status()
            location = self._parse_location(geocoding_response.json(), city)

            weather_response = await observed_request(
                client,
                "GET",
                FORECAST_URL,
                provider="open-meteo-weather",
                params={
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "current": (
                        "temperature_2m,apparent_temperature,precipitation,"
                        "weather_code,wind_speed_10m"
                    ),
                    "timezone": "auto",
                },
            )
            weather_response.raise_for_status()
            return self._parse_weather(weather_response.json(), location)
        except CityNotFoundError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise WeatherServiceError("天气服务暂时不可用，请稍后重试") from exc

    @staticmethod
    def _parse_location(payload: dict[str, Any], city: str) -> Location:
        results = payload.get("results") or []
        if not results:
            raise CityNotFoundError(f"未找到城市：{city}")
        item = results[0]
        return Location(
            name=item["name"],
            country=item.get("country"),
            admin1=item.get("admin1"),
            latitude=item["latitude"],
            longitude=item["longitude"],
        )

    @staticmethod
    def _parse_weather(payload: dict[str, Any], location: Location) -> CurrentWeather:
        current = payload["current"]
        weather_code = current["weather_code"]
        return CurrentWeather(
            location=location,
            temperature_c=current["temperature_2m"],
            apparent_temperature_c=current["apparent_temperature"],
            precipitation_mm=current["precipitation"],
            wind_speed_kmh=current["wind_speed_10m"],
            weather_code=weather_code,
            condition=weather_code_to_condition(weather_code),
            observed_at=current["time"],
        )
