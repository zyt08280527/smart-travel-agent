"""Re-rank a stored travel comparison without repeating provider requests."""

from datetime import datetime

from travel_agent.domain.decision import (
    JourneyContext,
    TravelComparisonResult,
    TravelMode,
    TravelOption,
    TravelPreferences,
)
from travel_agent.domain.transit import TransitOption as TransitRouteOption
from travel_agent.services.transit_selection import (
    normalize_transit_candidate,
    select_transit_candidate,
)
from travel_agent.services.travel_decision import (
    recommend_travel_mode,
    recommend_travel_variants,
)


def is_route_snapshot_fresh(
    context: JourneyContext,
    *,
    reference_at: datetime,
) -> bool:
    """Return whether a complete route snapshot is still inside its TTL."""
    if reference_at.tzinfo is None or reference_at.utcoffset() is None:
        raise ValueError("reference_at must include timezone information")
    snapshot_at = context.route_snapshot_at
    expires_at = context.route_snapshot_expires_at
    if snapshot_at is None or expires_at is None:
        return False
    comparable_reference = reference_at.astimezone(expires_at.tzinfo)
    return snapshot_at <= comparable_reference < expires_at


def build_route_refresh_args(
    context: JourneyContext,
    *,
    preference_updates: dict[str, object],
) -> dict[str, object]:
    """Build a composite planning call from stored journey facts."""
    if context.origin is None or context.destination is None:
        raise ValueError("上一轮规划结果缺少可复用的起终点坐标")

    preferences = TravelPreferences.model_validate(
        {
            **context.preferences.model_dump(),
            **preference_updates,
        }
    )
    return {
        "city": context.city,
        "origin_name": context.origin_name,
        "destination_name": context.destination_name,
        "origin_latitude": context.origin.latitude,
        "origin_longitude": context.origin.longitude,
        "destination_latitude": context.destination.latitude,
        "destination_longitude": context.destination.longitude,
        **preferences.model_dump(),
        "departure_time_text": (
            context.departure_time.source_text
            if context.departure_time is not None
            else None
        ),
        "arrival_time_text": (
            context.arrival_deadline.source_text
            if context.arrival_deadline is not None
            else None
        ),
        "arrival_buffer_minutes": (
            context.arrival_deadline.buffer_minutes
            if context.arrival_deadline is not None
            else 15
        ),
    }


def replan_from_payload(
    payload: dict[str, object],
    *,
    preference_updates: dict[str, object],
    transit_candidate_index: int | None = None,
    selected_mode: TravelMode | None = None,
) -> dict[str, object]:
    """Apply preference changes to a prior planning payload and score again."""
    context_value = payload.get("context")
    recommendation_value = payload.get("recommendation")
    if not isinstance(context_value, dict) or not isinstance(
        recommendation_value, dict
    ):
        raise ValueError("上一轮规划结果缺少上下文或推荐结果")

    context = JourneyContext.model_validate(context_value)
    ranked = recommendation_value.get("ranked_options")
    unavailable = recommendation_value.get("unavailable_options", [])
    if not isinstance(ranked, list) or not isinstance(unavailable, list):
        raise ValueError("上一轮规划结果缺少路线候选")

    options: list[TravelOption] = []
    for scored in ranked:
        if not isinstance(scored, dict) or not isinstance(scored.get("option"), dict):
            continue
        options.append(TravelOption.model_validate(scored["option"]))
    for raw_option in unavailable:
        if not isinstance(raw_option, dict):
            continue
        option = TravelOption.model_validate(raw_option)
        if option.status == "unavailable" and option.failure_reason and (
            option.failure_reason == "用户明确表示不能驾车"
            or "超过用户上限" in option.failure_reason
        ):
            option = option.model_copy(
                update={"status": "available", "failure_reason": None}
            )
        options.append(option)
    if not options:
        raise ValueError("上一轮规划结果没有可复用的路线候选")

    preferences = TravelPreferences.model_validate(
        {
            **context.preferences.model_dump(),
            **preference_updates,
        }
    )
    raw_transit_candidates = payload.get("transit_candidates", [])
    transit_candidates = (
        [
            TransitRouteOption.model_validate(item)
            for item in raw_transit_candidates
            if isinstance(item, dict)
        ]
        if isinstance(raw_transit_candidates, list)
        else []
    )
    if transit_candidate_index is not None and not transit_candidates:
        raise ValueError("上一轮规划结果没有公共交通候选")
    selected_transit_candidate_index = payload.get(
        "selected_transit_candidate_index"
    )
    if transit_candidates:
        previous_transit = next(
            (option for option in options if option.mode == "transit"),
            None,
        )
        if transit_candidate_index is not None and (
            previous_transit is None or not previous_transit.attribution
        ):
            raise ValueError("上一轮规划结果缺少公共交通路线信息")
        if previous_transit is not None and previous_transit.attribution:
            if transit_candidate_index is not None:
                if not 0 <= transit_candidate_index < len(transit_candidates):
                    raise ValueError("指定的公共交通候选不存在")
                candidate = transit_candidates[transit_candidate_index]
                if (
                    preferences.max_walking_distance_m is not None
                    and candidate.walking_distance_m
                    > preferences.max_walking_distance_m
                ):
                    raise ValueError("该公共交通候选超过当前最大步行距离")
                if (
                    preferences.max_transfer_count is not None
                    and candidate.transfer_count > preferences.max_transfer_count
                ):
                    raise ValueError("该公共交通候选超过当前最大换乘次数")
                selected_transit = normalize_transit_candidate(
                    candidate,
                    attribution=previous_transit.attribution,
                )
                selected_transit_candidate_index = transit_candidate_index
            else:
                selected_transit, selected_transit_candidate_index = (
                    select_transit_candidate(
                        transit_candidates,
                        attribution=previous_transit.attribution,
                        preferences=preferences,
                        weather=context.weather,
                    )
                )
            options = [
                selected_transit if option.mode == "transit" else option
                for option in options
            ]

    recommendation = recommend_travel_mode(
        options,
        preferences,
        weather=context.weather,
    )
    recommendation_variants = recommend_travel_variants(
        options,
        preferences,
        weather=context.weather,
    )
    old_limitations = recommendation_value.get("limitations", [])
    if isinstance(old_limitations, list):
        reusable_limitations = [
            item
            for item in old_limitations
            if isinstance(item, str) and "方案未参与评分：" not in item
        ]
        recommendation = recommendation.model_copy(
            update={
                "limitations": list(
                    dict.fromkeys(
                        [*recommendation.limitations, *reusable_limitations]
                    )
                )
            }
        )

    available_modes = {
        scored.option.mode for scored in recommendation.ranked_options
    }
    previous_selected_mode = payload.get("selected_mode")
    effective_selected_mode = selected_mode or (
        previous_selected_mode
        if previous_selected_mode in {"driving", "walking", "transit"}
        else None
    )
    if selected_mode is not None and selected_mode not in available_modes:
        raise ValueError("所选交通方式当前不可用")
    if effective_selected_mode not in available_modes:
        effective_selected_mode = None

    result = TravelComparisonResult(
        context=context.model_copy(update={"preferences": preferences}),
        recommendation=recommendation,
        recommendation_variants=recommendation_variants,
        transit_candidates=transit_candidates,
        selected_transit_candidate_index=selected_transit_candidate_index,
        selected_mode=effective_selected_mode,
    )
    return result.model_dump(mode="json")


def replan_from_stale_payload(
    payload: dict[str, object],
    *,
    preference_updates: dict[str, object],
) -> dict[str, object]:
    """Re-rank an expired snapshot after its deterministic refresh failed."""
    result = replan_from_payload(
        payload,
        preference_updates=preference_updates,
    )
    recommendation = result["recommendation"]
    if not isinstance(recommendation, dict):
        raise ValueError("上一轮规划结果缺少推荐结果")
    limitations = recommendation.get("limitations", [])
    if not isinstance(limitations, list):
        limitations = []
    stale_warning = (
        "路线自动刷新失败，以下结果基于已过期快照，仅供临时参考"
    )
    recommendation["limitations"] = list(
        dict.fromkeys([stale_warning, *limitations])
    )
    result["refresh_fallback"] = {
        "used_stale_snapshot": True,
        "reason": "route_refresh_failed",
    }
    return result
