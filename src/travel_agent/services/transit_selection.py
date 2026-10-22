"""Select one concrete transit route from provider alternatives."""

from datetime import datetime

from travel_agent.domain.decision import TravelOption, TravelPreferences
from travel_agent.domain.journey_time import ArrivalDeadline
from travel_agent.domain.transit import TransitOption as TransitRouteOption
from travel_agent.domain.weather import WeatherData
from travel_agent.services.travel_decision import (
    TravelDecisionError,
    apply_arrival_deadline,
    recommend_travel_mode,
)


def normalize_transit_candidate(
    candidate: TransitRouteOption,
    *,
    attribution: str,
) -> TravelOption:
    """Convert one provider candidate into the shared decision shape."""
    return TravelOption(
        mode="transit",
        distance_m=candidate.distance_m,
        duration_s=candidate.duration_s,
        walking_distance_m=candidate.walking_distance_m,
        cost_yuan=candidate.cost_yuan,
        transfer_count=candidate.transfer_count,
        has_subway=any(leg.mode == "subway" for leg in candidate.legs),
        geometry=candidate.geometry,
        attribution=attribution,
    )


def select_transit_candidate(
    candidates: list[TransitRouteOption],
    *,
    attribution: str,
    preferences: TravelPreferences,
    weather: WeatherData | None,
    arrival_deadline: ArrivalDeadline | None = None,
    reference_at: datetime | None = None,
) -> tuple[TravelOption, int]:
    """Apply hard constraints and user priority across transit alternatives."""
    if not candidates:
        raise ValueError("公交候选方案不能为空")
    normalized = [
        normalize_transit_candidate(candidate, attribution=attribution)
        for candidate in candidates
    ]
    normalized = apply_arrival_deadline(normalized, arrival_deadline, reference_at)

    try:
        recommendation = recommend_travel_mode(
            normalized,
            preferences,
            weather=weather,
        )
    except TravelDecisionError:
        # Preserve a real candidate so outer scoring reports the constraint failure.
        return normalized[0], 0
    selected = recommendation.ranked_options[0].option
    return selected, normalized.index(selected)
