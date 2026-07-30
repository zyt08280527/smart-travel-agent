"""Call the real transit service and print its normalized route plan."""

import asyncio
import json

from travel_agent.domain.route import GeoPoint
from travel_agent.services.transit import TransitService


async def main() -> None:
    """Plan one real transit route between two Shenzhen campuses."""
    origin = GeoPoint(latitude=22.5359023, longitude=113.9314749)
    destination = GeoPoint(latitude=22.6009872, longitude=113.987959)
    result = await TransitService().plan_transit_route(
        origin,
        destination,
        strategy=0,
        alternative_routes=3,
    )

    payload = {
        "mode": result.mode,
        "origin": result.origin.model_dump(),
        "destination": result.destination.model_dump(),
        "origin_city_code": result.origin_city_code,
        "destination_city_code": result.destination_city_code,
        "strategy": result.strategy,
        "option_count": len(result.options),
        "options": [
            {
                **option.model_dump(exclude={"legs"}),
                "leg_count": len(option.legs),
                "legs": [leg.model_dump() for leg in option.legs],
            }
            for option in result.options
        ],
        "attribution": result.attribution,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
