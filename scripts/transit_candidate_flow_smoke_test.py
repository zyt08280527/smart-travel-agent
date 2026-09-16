"""Validate the real public-transit strategy and candidate-selection flow."""

import argparse
import asyncio
import json
import sys
from typing import Any

from travel_agent.domain.decision import TravelPreferences
from travel_agent.domain.route import GeoPoint
from travel_agent.observability.http import capture_external_http_requests
from travel_agent.services.travel_planning import TravelPlanningService
from travel_agent.services.travel_replanning import replan_from_payload


def _candidate_summary(candidate: dict[str, Any], index: int) -> dict[str, Any]:
    legs = candidate.get("legs", [])
    line_names = list(
        dict.fromkeys(
            leg["line_name"]
            for leg in legs
            if isinstance(leg, dict)
            and isinstance(leg.get("line_name"), str)
            and leg["line_name"]
        )
    ) if isinstance(legs, list) else []
    return {
        "candidate_number": index + 1,
        "duration_s": candidate.get("duration_s"),
        "cost_yuan": candidate.get("cost_yuan"),
        "walking_distance_m": candidate.get("walking_distance_m"),
        "transfer_count": candidate.get("transfer_count"),
        "line_names": line_names,
    }


def _stage_summary(
    payload: dict[str, Any],
    *,
    external_http_request_count: int,
) -> dict[str, Any]:
    candidates = payload.get("transit_candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    context = payload.get("context", {})
    preferences = context.get("preferences", {}) if isinstance(context, dict) else {}
    return {
        "transit_strategy": (
            preferences.get("transit_strategy")
            if isinstance(preferences, dict)
            else None
        ),
        "external_http_request_count": external_http_request_count,
        "candidate_count": len(candidates),
        "selected_candidate_number": (
            payload.get("selected_transit_candidate_index", 0) + 1
            if isinstance(payload.get("selected_transit_candidate_index"), int)
            else None
        ),
        "candidates": [
            _candidate_summary(candidate, index)
            for index, candidate in enumerate(candidates)
            if isinstance(candidate, dict)
        ],
    }


def _manual_candidate_index(payload: dict[str, Any]) -> int:
    candidates = payload.get("transit_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("高德没有返回可选择的公共交通候选")
    selected = payload.get("selected_transit_candidate_index")
    if len(candidates) == 1:
        return 0
    return 1 if selected == 0 else 0


def _selected_candidate_contains_subway(payload: dict[str, Any]) -> bool:
    candidates = payload.get("transit_candidates")
    selected = payload.get("selected_transit_candidate_index")
    if (
        not isinstance(candidates, list)
        or not isinstance(selected, int)
        or not 0 <= selected < len(candidates)
    ):
        return False
    candidate = candidates[selected]
    if not isinstance(candidate, dict):
        return False
    legs = candidate.get("legs")
    return isinstance(legs, list) and any(
        isinstance(leg, dict) and leg.get("mode") == "subway" for leg in legs
    )


async def run(args: argparse.Namespace) -> None:
    origin = GeoPoint(
        latitude=args.origin_latitude,
        longitude=args.origin_longitude,
    )
    destination = GeoPoint(
        latitude=args.destination_latitude,
        longitude=args.destination_longitude,
    )
    service = TravelPlanningService()
    common_args = {
        "city": args.city,
        "origin_name": args.origin_name,
        "destination_name": args.destination_name,
        "origin": origin,
        "destination": destination,
    }

    with capture_external_http_requests() as initial_requests:
        initial = await service.compare(
            **common_args,
            preferences=TravelPreferences(transit_strategy="recommended"),
        )
    initial_payload = initial.model_dump(mode="json")

    with capture_external_http_requests() as strategy_requests:
        subway_first = await service.compare(
            **common_args,
            preferences=TravelPreferences(transit_strategy="subway_first"),
        )
    subway_payload = subway_first.model_dump(mode="json")
    candidates = subway_payload.get("transit_candidates", [])
    if not isinstance(candidates, list) or len(candidates) < args.minimum_candidates:
        raise RuntimeError(
            "公共交通候选数量不足："
            f"期望至少 {args.minimum_candidates} 条，实际 {len(candidates)} 条"
        )
    if not _selected_candidate_contains_subway(subway_payload):
        raise RuntimeError("地铁优先策略最终没有选中包含地铁的候选")

    manual_index = _manual_candidate_index(subway_payload)
    with capture_external_http_requests() as local_requests:
        manually_selected = replan_from_payload(
            subway_payload,
            preference_updates={},
            transit_candidate_index=manual_index,
        )
    if local_requests:
        raise RuntimeError("手动选择候选时发生了额外外部 HTTP 请求")
    if manually_selected.get("selected_transit_candidate_index") != manual_index:
        raise RuntimeError("手动选择的候选编号没有写回规划状态")

    report = {
        "journey": {
            "city": args.city,
            "origin_name": args.origin_name,
            "destination_name": args.destination_name,
        },
        "initial_recommended": _stage_summary(
            initial_payload,
            external_http_request_count=len(initial_requests),
        ),
        "subway_first_refresh": _stage_summary(
            subway_payload,
            external_http_request_count=len(strategy_requests),
        ),
        "manual_candidate_selection": _stage_summary(
            manually_selected,
            external_http_request_count=len(local_requests),
        ),
        "checks": {
            "strategy_refresh_used_external_services": bool(strategy_requests),
            "minimum_candidate_count_met": len(candidates)
            >= args.minimum_candidates,
            "subway_first_selected_subway": (
                _selected_candidate_contains_subway(subway_payload)
            ),
            "manual_selection_reused_snapshot": not local_requests,
            "manual_selection_persisted": (
                manually_selected.get("selected_transit_candidate_index")
                == manual_index
            ),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="深圳")
    parser.add_argument("--origin-name", default="深圳大学粤海校区")
    parser.add_argument("--destination-name", default="深圳大学丽湖校区")
    parser.add_argument("--origin-latitude", type=float, default=22.5359023)
    parser.add_argument("--origin-longitude", type=float, default=113.9314749)
    parser.add_argument("--destination-latitude", type=float, default=22.6009872)
    parser.add_argument("--destination-longitude", type=float, default=113.987959)
    parser.add_argument("--minimum-candidates", type=int, default=2)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
