"""Trace AMap coordinate conversion and city-code resolution."""

import asyncio
import json

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_city import AmapCityService
from travel_agent.services.amap_coordinate import AmapCoordinateService


def print_step(number: int, title: str, payload: object) -> None:
    """Print one readable trace step."""
    print(f"\n{'=' * 18} 步骤 {number}：{title} {'=' * 18}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def main() -> None:
    """Resolve city metadata for two Shenzhen University campuses."""
    wgs84_points = [
        GeoPoint(latitude=22.5359023, longitude=113.9314749),
        GeoPoint(latitude=22.6009872, longitude=113.987959),
    ]
    print_step(
        1,
        "项目内部 WGS84 起终点",
        [point.model_dump() for point in wgs84_points],
    )

    amap_points = await AmapCoordinateService().convert_wgs84(wgs84_points)
    print_step(
        2,
        "转换后的 GCJ-02 起终点",
        [point.model_dump() for point in amap_points],
    )

    city_service = AmapCityService()
    origin_city = await city_service.resolve_city(amap_points[0])
    print_step(3, "起点城市解析结果", origin_city.model_dump())

    destination_city = await city_service.resolve_city(amap_points[1])
    print_step(4, "终点城市解析结果", destination_city.model_dump())

    print_step(
        5,
        "公交路线接口所需城市参数",
        {
            "city1": origin_city.city_code,
            "city2": destination_city.city_code,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
