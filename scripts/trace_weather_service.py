"""逐步展示城市名如何变成 CurrentWeather，供学习 Service 层使用。"""

import asyncio
import json

import httpx

from travel_agent.services.weather import FORECAST_URL, GEOCODING_URL, WeatherService


def print_step(number: int, title: str, value: object) -> None:
    """以容易阅读的格式打印一个追踪步骤。"""
    print(f"\n{'=' * 18} 步骤 {number}：{title} {'=' * 18}")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


async def main() -> None:
    # 步骤 1～2：处理用户输入。
    raw_city = "  深圳  "
    print_step(1, "用户原始输入", repr(raw_city))

    normalized_city = raw_city.strip()
    print_step(2, "strip() 清理后的城市", repr(normalized_city))

    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # 步骤 3：城市名通过地理编码接口变成原始地点数据。
        geocoding_response = await client.get(
            GEOCODING_URL,
            params={
                "name": normalized_city,
                "count": 1,
                "language": "zh",
                "format": "json",
            },
        )
        print_step(3, "地理编码 HTTP 状态码", geocoding_response.status_code)
        geocoding_response.raise_for_status()
        geocoding_payload = geocoding_response.json()
        print_step(4, "地理编码接口的原始 JSON", geocoding_payload)

        # 步骤 4～5：Service 将外部字段转换为项目内部的 Location。
        location = WeatherService._parse_location(geocoding_payload, normalized_city)
        print_step(5, "转换并校验后的 Location", location.model_dump())

        # 步骤 6：用 Location 中的经纬度查询当前天气。
        weather_response = await client.get(
            FORECAST_URL,
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
        print_step(6, "天气 HTTP 状态码", weather_response.status_code)
        weather_response.raise_for_status()
        weather_payload = weather_response.json()
        print_step(7, "天气接口的 current 原始 JSON", weather_payload["current"])

        # 步骤 8～9：Service 将外部天气字段转换为 CurrentWeather。
        current_weather = WeatherService._parse_weather(weather_payload, location)
        print_step(8, "转换并校验后的 CurrentWeather", current_weather.model_dump())
        print_step(9, "可传输的最终 JSON 字符串", current_weather.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
