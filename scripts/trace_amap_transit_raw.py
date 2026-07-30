"""Inspect the shape of one real AMap transit response without exposing keys."""

import asyncio
import json
from typing import Any

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_city import AmapCityService
from travel_agent.services.amap_coordinate import AmapCoordinateService

AMAP_TRANSIT_URL = "https://restapi.amap.com/v5/direction/transit/integrated"


def describe_shape(value: Any, depth: int = 0) -> Any:
    """Keep keys and one list item while shortening large scalar values."""
    if depth >= 10:
        return f"<{type(value).__name__}>"
    if isinstance(value, dict):
        return {
            key: describe_shape(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return {
            "_type": "list",
            "_length": len(value),
            "_first": (
                describe_shape(value[0], depth + 1)
                if value
                else None
            ),
        }
    if isinstance(value, str) and len(value) > 120:
        return f"{value[:120]}...<length={len(value)}>"
    return value


async def main() -> None:
    """Request one transit plan and print a compact response shape."""
    settings = get_settings()
    wgs84_points = [
        GeoPoint(latitude=22.5359023, longitude=113.9314749),
        GeoPoint(latitude=22.6009872, longitude=113.987959),
    ]
    amap_points = await AmapCoordinateService().convert_wgs84(wgs84_points)

    city_service = AmapCityService()
    origin_city = await city_service.resolve_city(amap_points[0])
    destination_city = await city_service.resolve_city(amap_points[1])

    proxy_url = settings.route_proxy_url
    timeout = httpx.Timeout(20.0, connect=5.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            response = await client.get(
                AMAP_TRANSIT_URL,
                params={
                    "key": settings.amap_api_key.get_secret_value(),
                    "origin": (
                        f"{amap_points[0].longitude:.6f},"
                        f"{amap_points[0].latitude:.6f}"
                    ),
                    "destination": (
                        f"{amap_points[1].longitude:.6f},"
                        f"{amap_points[1].latitude:.6f}"
                    ),
                    "city1": origin_city.city_code,
                    "city2": destination_city.city_code,
                    "strategy": 0,
                    "AlternativeRoute": 3,
                    "show_fields": "cost",
                    "output": "json",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        print("高德公交路线请求失败；未输出请求URL或API Key。")
        return

    print(f"HTTP 状态码: {response.status_code}")
    if isinstance(payload, dict):
        print(f"业务状态: {payload.get('status')}")
        print(f"业务信息: {payload.get('info')}")
    print("\n公交响应结构（列表只展示第一项，长字符串已截断）：")
    print(json.dumps(describe_shape(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
