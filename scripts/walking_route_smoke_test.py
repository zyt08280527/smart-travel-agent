"""Call the real walking route service and print a compact summary."""

import argparse
import asyncio
import json

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_walking import AmapWalkingRouteService


async def run(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> None:
    """Plan one real walking route and print only model-relevant fields."""
    origin = GeoPoint(
        latitude=origin_latitude,
        longitude=origin_longitude,
    )
    destination = GeoPoint(
        latitude=destination_latitude,
        longitude=destination_longitude,
    )
    route = await AmapWalkingRouteService().plan_walking_route(
        origin,
        destination,
    )

    summary = {
        "mode": route.mode,
        "origin": route.origin.model_dump(),
        "destination": route.destination.model_dump(),
        "distance_m": route.distance_m,
        "duration_s": route.duration_s,
        "step_count": len(route.steps),
        "first_5_steps": [
            step.model_dump() for step in route.steps[:5]
        ],
        "geometry_point_count": len(route.geometry),
        "attribution": route.attribution,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    """Parse coordinates and run the real walking route request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin-latitude", type=float, default=22.5359023)
    parser.add_argument("--origin-longitude", type=float, default=113.9314749)
    parser.add_argument("--destination-latitude", type=float, default=22.6009872)
    parser.add_argument("--destination-longitude", type=float, default=113.987959)
    args = parser.parse_args()
    asyncio.run(
        run(
            origin_latitude=args.origin_latitude,
            origin_longitude=args.origin_longitude,
            destination_latitude=args.destination_latitude,
            destination_longitude=args.destination_longitude,
        )
    )


if __name__ == "__main__":
    main()
