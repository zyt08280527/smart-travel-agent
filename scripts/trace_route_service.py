"""逐步展示两个坐标如何变成 RoutePlan。"""

import asyncio
import json

import httpx

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint
from travel_agent.services.route import ROUTE_BASE_URL, ROUTE_USER_AGENT, RouteService


def print_step(number: int, title: str, value: object) -> None:
    """以容易阅读的格式打印一个追踪步骤。"""
    print(f"\n{'=' * 18} 步骤 {number}：{title} {'=' * 18}")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


async def main() -> None:
    """请求一次真实路线并展示 Service 转换前后的数据。"""
    origin = GeoPoint(latitude=22.5359023, longitude=113.9314749)
    destination = GeoPoint(latitude=22.6009872, longitude=113.987959)
    print_step(1, "起点 GeoPoint（深圳大学粤海校区）", origin.model_dump())
    print_step(2, "终点 GeoPoint（深圳大学丽湖校区）", destination.model_dump())

    coordinates = (
        f"{origin.longitude},{origin.latitude};"
        f"{destination.longitude},{destination.latitude}"
    )
    url = f"{ROUTE_BASE_URL}/{coordinates}"
    proxy_url = get_settings().route_proxy_url
    timeout = httpx.Timeout(15.0, connect=5.0)

    async with httpx.AsyncClient(
        timeout=timeout,
        proxy=str(proxy_url) if proxy_url is not None else None,
        trust_env=False,
    ) as client:
        response = await client.get(
            url,
            params={
                "steps": "true",
                "geometries": "geojson",
                "overview": "full",
            },
            headers={"User-Agent": ROUTE_USER_AGENT},
        )
        print_step(3, "httpx 组装后的最终请求 URL", str(response.request.url))
        print_step(4, "OSRM HTTP 状态码", response.status_code)
        response.raise_for_status()
        payload = response.json()

        raw_route = payload["routes"][0]
        raw_steps = [
            step for leg in raw_route["legs"] for step in leg["steps"]
        ]
        raw_coordinates = raw_route["geometry"]["coordinates"]
        raw_summary = {
            "code": payload["code"],
            "distance": raw_route["distance"],
            "duration": raw_route["duration"],
            "leg_count": len(raw_route["legs"]),
            "step_count": len(raw_steps),
            "first_5_steps": raw_steps[:5],
            "geometry_type": raw_route["geometry"]["type"],
            "geometry_point_count": len(raw_coordinates),
            "geometry_first_point": raw_coordinates[0],
            "geometry_last_point": raw_coordinates[-1],
        }
        print_step(5, "OSRM 原始 JSON 路线摘要", raw_summary)

        route = RouteService._parse_route(payload, origin, destination)
        normalized_summary = {
            "mode": route.mode,
            "distance_m": route.distance_m,
            "duration_s": route.duration_s,
            "step_count": len(route.steps),
            "geometry_point_count": len(route.geometry),
            "geometry_first_point": route.geometry[0].model_dump(),
            "geometry_last_point": route.geometry[-1].model_dump(),
            "attribution": route.attribution,
        }
        print_step(6, "转换并校验后的 RoutePlan 摘要", normalized_summary)
        print_step(
            7,
            "转换后的前 5 个 RouteStep",
            [step.model_dump() for step in route.steps[:5]],
        )


if __name__ == "__main__":
    asyncio.run(main())
