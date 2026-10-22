from datetime import datetime

import pytest

from travel_agent.domain.decision import TravelOption, TravelPreferences, update_preferences
from travel_agent.domain.journey_time import ArrivalDeadline
from travel_agent.domain.transit import TransitLeg, TransitOption
from travel_agent.services.transit_selection import select_transit_candidate
from travel_agent.services.travel_decision import recommend_travel_mode


def build_options() -> list[TravelOption]:
    return [
        TravelOption(mode="walking", distance_m=5000, duration_s=3600,
                     walking_distance_m=5000, cost_yuan=0, transfer_count=0, attribution="test"),
        TravelOption(mode="transit", distance_m=8000, duration_s=2400,
                     walking_distance_m=500, cost_yuan=5, transfer_count=1, attribution="test"),
        TravelOption(mode="driving", distance_m=10000, duration_s=1800,
                     walking_distance_m=0, cost_yuan=20, transfer_count=0, attribution="test"),
    ]


def test_legacy_preferences_are_migrated_and_explicit_priority_wins() -> None:
    assert TravelPreferences(transit_strategy="subway_first").priority == "subway_first"
    explicit = TravelPreferences(priority="fastest", transit_strategy="subway_first")
    assert explicit.transit_strategy == "recommended"
    reset = update_preferences(TravelPreferences(priority="subway_first"), {"priority": "balanced"})
    assert reset.priority == "balanced"
    assert reset.transit_strategy == "recommended"


def test_constraint_form_messages_support_setting_and_clearing_limits() -> None:
    from travel_agent.middleware.preference_replanning import parse_preference_updates

    assert parse_preference_updates("更新出行限制：不能驾车，最多步行800米，最多换乘0次") == {
        "can_drive": False, "max_walking_distance_m": 800, "max_transfer_count": 0,
    }
    assert parse_preference_updates("取消驾车限制，取消步行上限，取消换乘上限") == {
        "can_drive": None, "max_walking_distance_m": None, "max_transfer_count": None,
    }


def test_subway_priority_recommends_rail_even_when_driving_is_faster() -> None:
    options = build_options()
    options[1] = options[1].model_copy(update={"has_subway": True})
    result = recommend_travel_mode(options, TravelPreferences(priority="subway_first"))
    assert result.recommended_mode == "transit"
    assert result.ranked_options[0].option.has_subway
    assert "综合得分仅作辅助" in result.summary_reasons[0]


@pytest.mark.parametrize("has_subway,walking_limit", [(False, None), (True, 100)])
def test_subway_priority_falls_back_without_overriding_constraints(
    has_subway: bool, walking_limit: int | None,
) -> None:
    options = build_options()
    options[1] = options[1].model_copy(update={"has_subway": has_subway})
    result = recommend_travel_mode(options, TravelPreferences(
        priority="subway_first", max_walking_distance_m=walking_limit,
    ))
    assert result.recommended_mode == "driving"
    assert any("没有符合出行限制的地铁方案" in note for note in result.limitations)


def candidate(mode: str, duration: int) -> TransitOption:
    return TransitOption(
        distance_m=5000, duration_s=duration, walking_distance_m=200,
        transfer_count=0, cost_yuan=3,
        legs=[TransitLeg(mode=mode, distance_m=4800, duration_s=duration)],
    )


def test_arrival_deadline_filters_rail_before_selecting_transit_candidate() -> None:
    rail, bus = candidate("subway", 3600), candidate("bus", 600)
    selected, index = select_transit_candidate(
        [rail, bus], attribution="test", weather=None,
        preferences=TravelPreferences(priority="subway_first"),
        reference_at=datetime.fromisoformat("2026-10-22T10:00:00+08:00"),
        arrival_deadline=ArrivalDeadline(
            arrival_by=datetime.fromisoformat("2026-10-22T10:30:00+08:00"),
            precision="exact", source_text="十点半前到达", buffer_minutes=0,
        ),
    )
    assert index == 1
    assert selected.duration_s == 600


def test_manual_bus_choice_keeps_rail_recommendation_and_saves_bus() -> None:
    from tests.services.test_travel_replanning import build_payload
    from travel_agent.api.streaming import _planning_card_from_payload
    from travel_agent.middleware.itinerary_save_grounding import _ground_from_planning
    from travel_agent.services.travel_replanning import replan_from_payload

    payload = build_payload()
    payload["context"]["preferences"] = {"priority": "subway_first"}
    payload["transit_candidates"] = [
        candidate("subway", 3000).model_dump(mode="json"),
        candidate("bus", 1200).model_dump(mode="json"),
    ]
    result = replan_from_payload(
        payload, preference_updates={}, transit_candidate_index=1, selected_mode="transit",
    )
    assert result["recommended_transit_candidate_index"] == 0
    assert result["selected_transit_candidate_index"] == 1
    assert result["context"]["preferences"]["priority"] == "subway_first"
    card = _planning_card_from_payload(result)
    assert card is not None
    assert card.recommended_plan_id == "transit:0"
    assert card.selected_plan_id == "transit:1"
    saved = _ground_from_planning(result, {}, manual_transit_selection=False)
    assert saved["travel_mode"] == "transit"
    assert saved["duration_s"] == 1200
