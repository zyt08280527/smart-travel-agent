"""Call the real AMap driving service and print a safe normalized summary."""

import asyncio
import json
from collections import Counter

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_driving import AmapDrivingRouteService


async def main() -> None:
    """Plan one real traffic-aware route between two Shenzhen campuses."""
    origin = GeoPoint(latitude=22.5359023, longitude=113.9314749)
    destination = GeoPoint(latitude=22.6009872, longitude=113.987959)
    result = await AmapDrivingRouteService().plan_driving_route(
        origin,
        destination,
    )

    traffic_status_counts = Counter(
        segment.status for segment in result.traffic_segments
    )
    traffic_distance_by_status: Counter[str] = Counter()
    for segment in result.traffic_segments:
        traffic_distance_by_status[segment.status] += segment.distance_m

    payload = {
        "mode": result.mode,
        "distance_m": result.distance_m,
        "duration_s": result.duration_s,
        "duration_basis": result.duration_basis,
        "tolls_yuan": result.tolls_yuan,
        "taxi_cost_yuan": result.taxi_cost_yuan,
        "traffic_lights": result.traffic_lights,
        "restriction": result.restriction,
        "step_count": len(result.steps),
        "traffic_segment_count": len(result.traffic_segments),
        "traffic_status_counts": dict(traffic_status_counts),
        "traffic_distance_by_status_m": dict(traffic_distance_by_status),
        "attribution": result.attribution,
    }
    print("========== 高德真实驾车路线结果 ==========")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
