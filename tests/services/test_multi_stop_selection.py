from copy import deepcopy

import pytest
from pydantic import ValidationError

from tests.services.test_multi_stop_timeline import request_data, route_legs
from travel_agent.domain.multi_stop import LegCandidates, MultiStopRequest
from travel_agent.services.multi_stop_selection import (
    MultiStopSearchBudgetError,
    select_multi_stop_plans,
)


def scenario(priority="balanced"):
    data = request_data()
    data["stops"] = [data["stops"][0], data["stops"][1], data["stops"][-1]]
    data["preferences"] = {"priority": priority}
    request = MultiStopRequest.model_validate(data)
    bases = route_legs(request)
    groups = []
    for base in bases:
        candidates = []
        for name, minutes, cost, walking, transfers, subway, mode in [
            ("drive", 10, 30, 0, 0, False, "driving"),
            ("bus", 25, 2, 300, 0, False, "transit"),
            ("rail", 30, 5, 100, 1, True, "transit"),
        ]:
            candidates.append(
                base.model_copy(
                    deep=True,
                    update={
                        "candidate_id": name,
                        "option": base.option.model_copy(
                            update={
                                "mode": mode,
                                "duration_s": minutes * 60,
                                "cost_yuan": cost,
                                "walking_distance_m": walking,
                                "transfer_count": transfers,
                                "has_subway": subway,
                            }
                        ),
                    },
                )
            )
        groups.append(
            LegCandidates(
                origin_stop_id=base.origin_stop_id,
                destination_stop_id=base.destination_stop_id,
                candidates=candidates,
            )
        )
    return request, groups


def selected_ids(result):
    return tuple(item.leg.candidate_id for item in result.plans[0].evaluation.legs)


def test_checks_all_combinations_and_only_ranks_feasible_complete_plans():
    request, groups = scenario()
    result = select_multi_stop_plans(request, groups)
    assert result.status == "feasible"
    assert result.evaluated_combinations == 9
    assert result.feasible_combinations == 5
    assert result.rejected_combinations == 4
    assert result.insufficient_combinations == 0
    assert result.recommended_plan_id == result.plans[0].plan_id
    assert all(plan.evaluation.status == "feasible" for plan in result.plans)
    assert result.issue_counts["driving_continuity"] == 4


@pytest.mark.parametrize(
    "priority,expected",
    [
        ("fastest", ("drive", "drive")),
        ("cheapest", ("bus", "bus")),
        ("least_walking", ("drive", "drive")),
        ("fewest_transfers", ("drive", "drive")),
        ("subway_first", ("rail", "rail")),
    ],
)
def test_six_priority_semantics_without_reordering(priority, expected):
    request, groups = scenario(priority)
    result = select_multi_stop_plans(request, groups)
    assert selected_ids(result) == expected
    assert [item.stop_id for item in result.plans[0].evaluation.stops] == [
        stop.stop_id for stop in request.stops
    ]


def test_hard_constraints_override_subway_and_fallback_is_explained():
    request, groups = scenario("subway_first")
    request.preferences.max_transfer_count = 0
    result = select_multi_stop_plans(request, groups)
    assert "rail" not in selected_ids(result)
    assert all(plan.subway_leg_count == 0 for plan in result.plans)
    assert any("没有满足全程硬限制的地铁组合" in text for text in result.limitations)


def test_arrival_deadline_eliminates_cheap_and_subway_combinations():
    request, groups = scenario("cheapest")
    request.arrival_deadline.arrival_by = request.departure_time.departure_at.replace(hour=10)
    result = select_multi_stop_plans(request, groups)
    assert selected_ids(result) == ("drive", "drive")
    assert result.feasible_combinations == 1
    assert "arrival_deadline" in result.issue_counts


def test_unknown_cost_never_outranks_known_complete_cost_in_cheapest_mode():
    request, groups = scenario("cheapest")
    for group in groups:
        group.candidates[0].option.cost_yuan = None
    result = select_multi_stop_plans(request, groups, limit=10)
    assert selected_ids(result) == ("bus", "bus")
    assert result.plans[-1].evaluation.totals.total_cost_yuan is None
    assert result.plans[0].evaluation.totals.total_cost_yuan == 4


def test_all_costs_unknown_fall_back_without_claiming_zero_or_cheapest():
    request, groups = scenario("cheapest")
    for group in groups:
        for candidate in group.candidates:
            candidate.option.cost_yuan = None
    result = select_multi_stop_plans(request, groups)
    assert all(plan.evaluation.totals.total_cost_yuan is None for plan in result.plans)
    assert any("无法按费用比较" in text for text in result.limitations)


def test_permutation_of_candidate_input_order_cannot_change_recommendation_or_scores():
    request, groups = scenario()
    before = deepcopy((request.model_dump(), [group.model_dump() for group in groups]))
    first = select_multi_stop_plans(request, groups, limit=10)
    assert before == (request.model_dump(), [group.model_dump() for group in groups])
    for group in groups:
        group.candidates.reverse()
    assert select_multi_stop_plans(request, groups, limit=10) == first


def test_search_budget_overflow_is_explicit_and_never_a_false_no_route():
    request, groups = scenario()
    with pytest.raises(MultiStopSearchBudgetError, match="组合数"):
        select_multi_stop_plans(request, groups, max_combinations=8)
    with pytest.raises(MultiStopSearchBudgetError, match="候选超过预算"):
        select_multi_stop_plans(request, groups, max_candidates_per_leg=2)


def test_missing_segment_returns_insufficient_data_with_no_recommended_plan():
    request, groups = scenario()
    groups[1].candidates = []
    result = select_multi_stop_plans(request, groups)
    assert result.status == "insufficient_data"
    assert result.plans == []
    assert result.recommended_plan_id is None
    assert result.evaluated_combinations == 0
    assert result.issue_examples[0].stop_index == 2


def test_infeasible_and_insufficient_combinations_are_distinguished():
    request, groups = scenario()
    request.preferences.can_drive = False
    request.preferences.max_walking_distance_m = 50
    first = select_multi_stop_plans(request, groups)
    assert first.status == "infeasible"
    for group in groups:
        for leg in group.candidates:
            leg.option.walking_distance_m = None
    second = select_multi_stop_plans(request, groups)
    assert second.status == "insufficient_data"
    assert second.insufficient_combinations == 4
    assert second.plans == []


def test_duplicate_ids_misaligned_groups_and_invalid_limits_are_rejected():
    request, groups = scenario()
    duplicate = groups[0].model_dump()
    duplicate["candidates"].append(duplicate["candidates"][0])
    with pytest.raises(ValidationError):
        LegCandidates.model_validate(duplicate)
    with pytest.raises(ValueError, match="相邻地点"):
        select_multi_stop_plans(request, list(reversed(groups)))
    with pytest.raises(ValueError, match="分段数"):
        select_multi_stop_plans(request, groups[:-1])
    with pytest.raises(ValueError):
        select_multi_stop_plans(request, groups, limit=0)


def test_subway_priority_uses_feasible_coverage_and_never_ignores_global_walking_limit():
    request, groups = scenario("subway_first")
    request.preferences.can_drive = False
    request.preferences.max_walking_distance_m = 450
    groups[0].candidates[2].option.walking_distance_m = 200
    groups[1].candidates[2].option.walking_distance_m = 300
    result = select_multi_stop_plans(request, groups)
    assert result.status == "infeasible"
    assert result.plans == []


def test_subway_priority_chooses_one_rail_leg_when_two_would_violate_global_limit():
    request, groups = scenario("subway_first")
    request.preferences.can_drive = False
    request.preferences.max_walking_distance_m = 800
    for group in groups:
        group.candidates[2].option.walking_distance_m = 500
    result = select_multi_stop_plans(request, groups)
    assert result.status == "feasible"
    assert result.plans[0].subway_leg_count == 1
    assert result.plans[0].evaluation.totals.walking_distance_m == 800


def test_manual_plan_outside_shortlist_is_added_without_changing_recommendation():
    request, groups = scenario("fastest")
    original = select_multi_stop_plans(request, groups, limit=1)
    chosen = select_multi_stop_plans(
        request, groups, limit=1, selected_candidate_ids=["bus", "rail"]
    )
    assert len(chosen.plans) == 2
    assert chosen.recommended_plan_id == original.recommended_plan_id
    assert chosen.selected_plan_id == chosen.plans[1].plan_id
    assert chosen.selected_plan_id != chosen.recommended_plan_id
