"""Select one concrete transit route from provider alternatives."""

from travel_agent.domain.decision import TravelOption, TravelPreferences
from travel_agent.domain.transit import TransitOption as TransitRouteOption
from travel_agent.domain.weather import WeatherData
from travel_agent.services.travel_decision import (
    TravelDecisionError,
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
        geometry=candidate.geometry,
        attribution=attribution,
    )


def select_transit_candidate(
    candidates: list[TransitRouteOption],
    *,
    attribution: str,
    preferences: TravelPreferences,
    weather: WeatherData | None,
) -> tuple[TravelOption, int]:
    """Apply hard constraints and user priority across transit alternatives."""
    if not candidates:
        raise ValueError("公交候选方案不能为空")
    normalized = [
        normalize_transit_candidate(candidate, attribution=attribution)
        for candidate in candidates
    ]
    selection_preferences = preferences
    if preferences.transit_strategy in {"fewest_transfers", "least_walking"}:
        selection_preferences = preferences.model_copy(
            update={"priority": preferences.transit_strategy}
        )

    ranked_pool = normalized
    if preferences.transit_strategy == "subway_first":
        subway_options = [
            option
            for option, candidate in zip(normalized, candidates, strict=True)
            if any(leg.mode == "subway" for leg in candidate.legs)
        ]
        if subway_options:
            ranked_pool = subway_options

    try:
        recommendation = recommend_travel_mode(
            ranked_pool,
            selection_preferences,
            weather=weather,
        )
    except TravelDecisionError:
        if ranked_pool is not normalized:
            try:
                recommendation = recommend_travel_mode(
                    normalized,
                    selection_preferences,
                    weather=weather,
                )
            except TravelDecisionError:
                recommendation = None
        else:
            recommendation = None
        if recommendation is None:
            # Preserve a real candidate so the outer mode-level decision can
            # return its normal, user-facing hard-constraint rejection.
            return normalized[0], 0
    selected = recommendation.ranked_options[0].option
    return selected, normalized.index(selected)
