"""Offline synthetic example: fixed order, visit windows, stays and arrival deadline."""

import argparse
import json

from travel_agent.domain.multi_stop import MultiStopLeg, MultiStopRequest
from travel_agent.services.multi_stop_timeline import evaluate_fixed_order


def run_example(extra_stay_minutes: float = 0) -> dict:
    """All locations, durations and prices here are fixtures, not provider results."""
    names = ["示例起点", "示例办事点", "示例取件点", "示例终点"]
    stays = [0, 40 + extra_stay_minutes, 10, 0]
    request = MultiStopRequest.model_validate({
        "city_code": "0755",
        "departure_time": {
            "departure_at": "2026-10-24T09:00:00+08:00", "precision": "exact",
            "source_text": "周六九点出发",
        },
        "arrival_deadline": {
            "arrival_by": "2026-10-24T13:00:00+08:00", "precision": "exact",
            "source_text": "十三点前到达", "buffer_minutes": 0,
        },
        "preferences": {"can_drive": False, "max_walking_distance_m": 1000},
        "stops": [{
            "stop_id": str(index), "name": name, "city_code": "0755",
            "point": {"latitude": 22.5 + index * 0.01, "longitude": 114},
            "stay_minutes": stays[index],
            **({"earliest_arrival_at": "2026-10-24T10:00:00+08:00"} if index == 1 else {}),
        } for index, name in enumerate(names)],
    })
    legs = [MultiStopLeg(
        origin_stop_id=str(index), destination_stop_id=str(index + 1),
        candidate_id=f"fixture:{index}", source="synthetic_fixture",
        fetched_at="2026-10-22T10:00:00+08:00",
        option={
            "mode": "transit", "distance_m": 5000, "duration_s": 1800,
            "walking_distance_m": 200, "transfer_count": 0,
            "cost_yuan": None if index == 1 else 3,
            "attribution": "固定示例数据，不是真实路线或票价",
        },
    ) for index in range(3)]
    result = evaluate_fixed_order(request, legs)
    return {
        "data_basis": "synthetic_fixture_only_no_external_calls",
        "request": request.model_dump(mode="json"),
        "evaluation": result.model_dump(mode="json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extra-stay-minutes", type=float, default=0,
                        help="Increase the first visit's stay to inspect downstream lateness")
    args = parser.parse_args()
    print(json.dumps(run_example(args.extra_stay_minutes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
