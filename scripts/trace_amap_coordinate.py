"""Trace WGS84-to-GCJ-02 conversion for two route endpoints."""

import asyncio
import json

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_coordinate import AmapCoordinateService


def print_step(number: int, title: str, payload: object) -> None:
    """Print one readable trace step."""
    print(f"\n{'=' * 18} 步骤 {number}：{title} {'=' * 18}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def main() -> None:
    """Convert the two Shenzhen University campus coordinates once."""
    wgs84_points = [
        GeoPoint(latitude=22.5359023, longitude=113.9314749),
        GeoPoint(latitude=22.6009872, longitude=113.987959),
    ]
    print_step(
        1,
        "项目内部的 WGS84 坐标",
        [point.model_dump() for point in wgs84_points],
    )

    converted = await AmapCoordinateService().convert_wgs84(wgs84_points)
    print_step(
        2,
        "高德返回的 GCJ-02 坐标",
        [point.model_dump() for point in converted],
    )

    changes = [
        {
            "longitude_change": converted_point.longitude
            - wgs84_point.longitude,
            "latitude_change": converted_point.latitude
            - wgs84_point.latitude,
        }
        for wgs84_point, converted_point in zip(
            wgs84_points,
            converted,
            strict=True,
        )
    ]
    print_step(3, "经纬度数值变化", changes)


if __name__ == "__main__":
    asyncio.run(main())
