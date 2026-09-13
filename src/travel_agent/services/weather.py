from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from travel_agent.domain.journey_time import DepartureTime
from travel_agent.domain.weather import (
    CurrentWeather,
    ForecastWeather,
    Location,
    WeatherData,
)
from travel_agent.observability.http import observed_request

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CITY_ADMINISTRATIVE_SUFFIXES = ("特别行政区", "自治州", "地区", "市")

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


class WeatherTimeUnsupportedError(WeatherServiceError):
    """Raised when the requested departure cannot use current or forecast data."""


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

    async def get_weather_for_departure(
        self,
        city: str,
        departure: DepartureTime,
        *,
        reference_at: datetime,
    ) -> WeatherData:
        """Choose observed current weather or an hourly departure forecast."""
        query_kind = departure.weather_query_kind(reference_at)
        if query_kind == "past":
            raise WeatherTimeUnsupportedError("出发时间已经过去，请提供新的出发时间")
        if query_kind == "out_of_range":
            raise WeatherTimeUnsupportedError(
                "出发时间超出当前可查询的16天天气预报范围"
            )
        if query_kind == "current":
            return await self.get_current_weather(city)
        return await self.get_forecast_weather(city, departure)

    async def get_forecast_weather(
        self,
        city: str,
        departure: DepartureTime,
    ) -> ForecastWeather:
        """Fetch the hourly forecast closest to the resolved departure time."""
        normalized_city = city.strip()
        if not normalized_city:
            raise ValueError("城市名称不能为空")

        if self._external_client is not None:
            return await self._fetch_forecast(
                self._external_client,
                normalized_city,
                departure,
            )

        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await self._fetch_forecast(client, normalized_city, departure)

    async def _fetch(self, client: httpx.AsyncClient, city: str) -> CurrentWeather:
        try:
            location = await self._resolve_location(client, city)

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

    async def _fetch_forecast(
        self,
        client: httpx.AsyncClient,
        city: str,
        departure: DepartureTime,
    ) -> ForecastWeather:
        try:
            location = await self._resolve_location(client, city)
            target_date = departure.departure_at.date().isoformat()

            weather_response = await observed_request(
                client,
                "GET",
                FORECAST_URL,
                provider="open-meteo-forecast",
                params={
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "hourly": (
                        "temperature_2m,apparent_temperature,precipitation,"
                        "weather_code,wind_speed_10m"
                    ),
                    "timezone": departure.timezone,
                    "start_date": target_date,
                    "end_date": target_date,
                },
            )
            weather_response.raise_for_status()
            return self._parse_forecast(
                weather_response.json(),
                location,
                departure,
            )
        except CityNotFoundError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise WeatherServiceError("天气预报服务暂时不可用，请稍后重试") from exc

    async def _resolve_location(
        self,
        client: httpx.AsyncClient,
        city: str,
    ) -> Location:
        """Resolve a city, retrying without Chinese administrative suffixes."""
        candidates = [city]
        for suffix in CITY_ADMINISTRATIVE_SUFFIXES:
            if city.endswith(suffix) and len(city) > len(suffix):
                candidates.append(city[: -len(suffix)])
                break

        for candidate in candidates:
            response = await observed_request(
                client,
                "GET",
                GEOCODING_URL,
                provider="open-meteo-geocoding",
                params={
                    "name": candidate,
                    "count": 1,
                    "language": "zh",
                    "format": "json",
                },
            )
            response.raise_for_status()
            try:
                return self._parse_location(response.json(), candidate)
            except CityNotFoundError:
                continue
        raise CityNotFoundError(f"未找到城市：{city}")

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

    @staticmethod
    def _parse_forecast(
        payload: dict[str, Any],
        location: Location,
        departure: DepartureTime,
    ) -> ForecastWeather:
        hourly = payload["hourly"]
        timezone = ZoneInfo(departure.timezone)
        forecast_times = [
            datetime.fromisoformat(value).replace(tzinfo=timezone)
            for value in hourly["time"]
        ]
        if not forecast_times:
            raise ValueError("天气预报没有返回逐小时数据")
        index = min(
            range(len(forecast_times)),
            key=lambda item: abs(
                forecast_times[item] - departure.departure_at
            ),
        )
        weather_code = hourly["weather_code"][index]
        return ForecastWeather(
            location=location,
            temperature_c=hourly["temperature_2m"][index],
            apparent_temperature_c=hourly["apparent_temperature"][index],
            precipitation_mm=hourly["precipitation"][index],
            wind_speed_kmh=hourly["wind_speed_10m"][index],
            weather_code=weather_code,
            condition=weather_code_to_condition(weather_code),
            forecast_at=hourly["time"][index],
        )
