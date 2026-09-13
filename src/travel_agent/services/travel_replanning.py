"""Re-rank a stored travel comparison without repeating provider requests."""

from travel_agent.domain.decision import (
    JourneyContext,
    TravelComparisonResult,
    TravelOption,
    TravelPreferences,
)
from travel_agent.services.travel_decision import (
    recommend_travel_mode,
    recommend_travel_variants,
)


def replan_from_payload(
    payload: dict[str, object],
    *,
    preference_updates: dict[str, object],
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
        if option.status == "unavailable":
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

    result = TravelComparisonResult(
        context=context.model_copy(update={"preferences": preferences}),
        recommendation=recommendation,
        recommendation_variants=recommendation_variants,
    )
    return result.model_dump(mode="json")
