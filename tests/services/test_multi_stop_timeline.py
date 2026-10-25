from copy import deepcopy

import pytest
from pydantic import ValidationError

from travel_agent.domain.multi_stop import MultiStopLeg, MultiStopRequest
from travel_agent.services.multi_stop_timeline import evaluate_fixed_order


def request_data():
    return {
        "city_code": "0755",
        "departure_time": {
            "departure_at": "2026-10-24T09:00:00+08:00", "precision": "exact",
            "source_text": "周六九点出发",
        },
        "stops": [
            {"stop_id": stop_id, "name": name, "city_code": "0755",
             "point": {"latitude": 22.5, "longitude": 114}, "stay_minutes": stay}
            for stop_id, name, stay in [
                ("start", "出发点", 0), ("hospital", "办事点", 40),
                ("parcel", "取件点", 10), ("end", "终点", 0),
            ]
        ],
        "arrival_deadline": {"arrival_by": "2026-10-24T13:00:00+08:00",
                             "precision": "exact", "source_text": "一点前到终点",
                             "buffer_minutes": 0},
    }


def route_legs(request, *, modes=None, minutes=None):
    count = len(request.stops) - 1
    modes = modes or ["transit"] * count
    minutes = minutes or [30] * count
    return [MultiStopLeg(
        origin_stop_id=request.stops[index].stop_id,
        destination_stop_id=request.stops[index + 1].stop_id,
        candidate_id=f"fixture:{index}", source="synthetic_fixture",
        fetched_at="2026-10-22T10:00:00+08:00",
        option={"mode": modes[index], "distance_m": 1000, "duration_s": minutes[index] * 60,
                "walking_distance_m": 100, "transfer_count": 0, "cost_yuan": 3,
                "attribution": "固定测试数据，不是真实路线"},
    ) for index in range(count)]


def evaluate(data=None, **kwargs):
    request = MultiStopRequest.model_validate(data or request_data())
    return evaluate_fixed_order(request, route_legs(request, **kwargs))


def test_fixed_order_propagates_stays_and_preserves_inputs_and_route_evidence():
    request = MultiStopRequest.model_validate(request_data())
    legs = route_legs(request)
    original = deepcopy((request.model_dump(), [leg.model_dump() for leg in legs]))
    result = evaluate_fixed_order(request, legs)
    assert result.status == "feasible"
    assert [stop.stop_id for stop in result.stops] == [stop.stop_id for stop in request.stops]
    assert result.stops[1].arrival_at.isoformat() == "2026-10-24T09:30:00+08:00"
    assert result.stops[1].departure_at.isoformat() == "2026-10-24T10:10:00+08:00"
    assert result.stops[-1].arrival_at.isoformat() == "2026-10-24T11:20:00+08:00"
    assert result.totals.travel_s == 5400
    assert result.totals.stay_s == 3000
    assert result.totals.elapsed_s == 8400
    assert result.legs[0].leg == legs[0]
    assert original == (request.model_dump(), [leg.model_dump() for leg in legs])
    assert evaluate_fixed_order(request, legs) == result


def test_early_arrival_waits_before_stay_and_delays_all_later_legs():
    data = request_data()
    data["stops"][1]["earliest_arrival_at"] = "2026-10-24T10:00:00+08:00"
    result = evaluate(data)
    assert result.stops[1].physical_arrival_at.hour == 9
    assert result.stops[1].arrival_at.hour == 10
    assert result.stops[1].waiting_s == 1800
    assert result.stops[1].departure_at.minute == 40
    assert result.stops[-1].arrival_at.isoformat() == "2026-10-24T11:50:00+08:00"
    assert result.totals.waiting_s == 1800


def test_exact_window_and_deadline_boundary_are_inclusive():
    data = request_data()
    data["stops"][1].update({"earliest_arrival_at": "2026-10-24T09:30:00+08:00",
                             "latest_arrival_at": "2026-10-24T09:30:00+08:00"})
    data["arrival_deadline"]["arrival_by"] = "2026-10-24T11:20:00+08:00"
    assert evaluate(data).status == "feasible"
    data["arrival_deadline"]["buffer_minutes"] = 1
    result = evaluate(data)
    assert result.status == "infeasible"
    issue = next(issue for issue in result.issues if issue.code == "arrival_deadline")
    assert issue.delay_s == 60
    assert issue.stop_index == 3


def test_late_station_has_specific_evidence_and_does_not_hide_later_timeline():
    data = request_data()
    data["stops"][1]["latest_arrival_at"] = "2026-10-24T09:20:00+08:00"
    result = evaluate(data)
    assert result.status == "infeasible"
    assert len(result.stops) == 4
    assert result.issues[0].code == "arrival_window"
    assert result.issues[0].stop_index == 1
    assert result.issues[0].delay_s == 600
    assert "办事点" in result.issues[0].message


@pytest.mark.parametrize("middle_count", [1, 2, 3])
def test_one_to_three_intermediate_stops(middle_count):
    data = request_data()
    prototype = data["stops"][1]
    data["stops"] = [data["stops"][0], *[
        {**prototype, "stop_id": f"visit-{index}"} for index in range(middle_count)
    ], data["stops"][-1]]
    data.pop("arrival_deadline")
    result = evaluate(data)
    assert result.status == "feasible"
    assert len(result.legs) == middle_count + 1


@pytest.mark.parametrize("invalid", ["too_few", "too_many", "city", "duplicate",
                                     "origin_stay", "end_stay", "window", "naive", "vague"])
def test_scope_and_invalid_inputs_are_rejected(invalid):
    data = request_data()
    if invalid == "too_few":
        data["stops"] = [data["stops"][0], data["stops"][-1]]
    elif invalid == "too_many":
        data["stops"] += [{**data["stops"][1], "stop_id": str(index)} for index in range(3)]
    elif invalid == "city":
        data["stops"][1]["city_code"] = "020"
    elif invalid == "duplicate":
        data["stops"][1]["stop_id"] = "start"
    elif invalid == "origin_stay":
        data["stops"][0]["stay_minutes"] = 1
    elif invalid == "end_stay":
        data["stops"][-1]["stay_minutes"] = 1
    elif invalid == "window":
        data["stops"][1].update({"earliest_arrival_at": "2026-10-24T11:00:00+08:00",
                                 "latest_arrival_at": "2026-10-24T10:00:00+08:00"})
    elif invalid == "naive":
        data["stops"][1]["earliest_arrival_at"] = "2026-10-24T11:00:00"
    else:
        data["departure_time"]["precision"] = "date_only"
    with pytest.raises(ValidationError):
        MultiStopRequest.model_validate(data)


def test_segment_alignment_and_missing_segment_are_input_errors():
    request = MultiStopRequest.model_validate(request_data())
    legs = route_legs(request)
    with pytest.raises(ValueError, match="分段数"):
        evaluate_fixed_order(request, legs[:-1])
    with pytest.raises(ValueError, match="相邻地点"):
        evaluate_fixed_order(request, [legs[1], legs[0], legs[2]])


def test_unavailable_route_does_not_generate_fictional_total_or_later_arrivals():
    request = MultiStopRequest.model_validate(request_data())
    legs = route_legs(request)
    legs[1] = legs[1].model_copy(update={"option": legs[1].option.model_copy(update={
        "status": "failed", "duration_s": None, "failure_reason": "服务超时",
    })})
    result = evaluate_fixed_order(request, legs)
    assert result.status == "insufficient_data"
    assert result.totals is None
    assert len(result.stops) == 2
    assert len(result.legs) == 1
    assert result.issues[-1].code == "route_unavailable"
    assert result.issues[-1].stop_index == 2


def test_unknown_cost_is_not_zero_and_does_not_invalidate_time_feasibility():
    request = MultiStopRequest.model_validate(request_data())
    legs = route_legs(request)
    legs[0].option.cost_yuan = None
    result = evaluate_fixed_order(request, legs)
    assert result.status == "feasible"
    assert result.totals.total_cost_yuan is None
    assert result.totals.known_cost_subtotal_yuan == 6
    assert any("小计不是全程总费用" in text for text in result.limitations)


@pytest.mark.parametrize("unknown", [False, True])
def test_global_walking_limit_sums_legs_and_never_assumes_missing_is_zero(unknown):
    data = request_data()
    data["preferences"] = {"max_walking_distance_m": 250}
    request = MultiStopRequest.model_validate(data)
    legs = route_legs(request)
    if unknown:
        legs[1].option.walking_distance_m = None
    result = evaluate_fixed_order(request, legs)
    assert result.status == ("insufficient_data" if unknown else "infeasible")
    assert result.issues[-1].code == ("walking_unknown" if unknown else "walking_limit")


def test_global_transfers_and_walk_transit_combinations():
    data = request_data()
    data["preferences"] = {"max_transfer_count": 1}
    request = MultiStopRequest.model_validate(data)
    legs = route_legs(request, modes=["walking", "transit", "transit"])
    legs[0].option.transfer_count = None
    legs[1].option.transfer_count = 1
    legs[2].option.transfer_count = 1
    result = evaluate_fixed_order(request, legs)
    assert result.status == "infeasible"
    assert result.totals.transfer_count == 2
    assert result.totals.walking_distance_m == 1200
    assert result.issues[-1].code == "transfer_limit"
    legs[1].option.transfer_count = None
    result = evaluate_fixed_order(request, legs)
    assert result.status == "insufficient_data"
    assert result.issues[-1].code == "transfers_unknown"


@pytest.mark.parametrize("modes", [["driving", "walking", "driving"],
                                   ["walking", "driving", "driving"],
                                   ["driving", "transit", "transit"]])
def test_car_cannot_teleport_and_parking_transfers_are_out_of_scope(modes):
    result = evaluate(modes=modes)
    assert result.status == "infeasible"
    assert result.issues[0].code == "driving_continuity"


def test_all_driving_is_valid_unless_user_cannot_drive():
    assert evaluate(modes=["driving"] * 3).status == "feasible"
    data = request_data()
    data["preferences"] = {"can_drive": False}
    result = evaluate(data, modes=["driving"] * 3)
    assert result.status == "infeasible"
    assert result.issues[0].code == "driving_not_allowed"


def test_changing_stay_updates_all_later_arrivals_without_reordering():
    data = request_data()
    before = evaluate(data)
    data["stops"][1]["stay_minutes"] += 30
    after = evaluate(data)
    assert after.stops[1].arrival_at == before.stops[1].arrival_at
    assert (after.stops[-1].arrival_at - before.stops[-1].arrival_at).total_seconds() == 1800
    assert [stop.stop_id for stop in before.stops] == [stop.stop_id for stop in after.stops]


def test_midnight_and_different_input_offsets_use_real_instants():
    data = request_data()
    data["departure_time"]["departure_at"] = "2026-10-24T23:30:00+08:00"
    data["arrival_deadline"]["arrival_by"] = "2026-10-24T18:00:00+00:00"
    result = evaluate(data)
    assert result.status == "feasible"
    assert result.stops[-1].arrival_at.isoformat() == "2026-10-25T01:50:00+08:00"


def test_dst_fall_back_counts_elapsed_time_in_utc():
    data = request_data()
    data["departure_time"].update({"departure_at": "2026-11-01T01:30:00-04:00",
                                    "timezone": "America/New_York"})
    data.pop("arrival_deadline")
    result = evaluate(data)
    assert result.status == "feasible"
    assert result.stops[1].arrival_at.isoformat() == "2026-11-01T01:00:00-05:00"
    assert result.totals.elapsed_s == 8400
    assert result.legs[0].departure_at.timestamp() < result.legs[0].arrival_at.timestamp()


def test_nonfinite_nested_route_numbers_are_rejected():
    request = MultiStopRequest.model_validate(request_data())
    legs = route_legs(request)
    legs[0].option.duration_s = float("inf")
    with pytest.raises(ValueError, match="有限数值"):
        evaluate_fixed_order(request, legs)


def test_revisiting_one_poi_is_allowed_with_different_visit_ids():
    data = request_data()
    data["stops"][0]["provider_place_id"] = "same-poi"
    data["stops"][-1]["provider_place_id"] = "same-poi"
    assert evaluate(data).status == "feasible"
def test_untimed_trip_keeps_all_legs_and_stays_without_inventing_dates():
    data = request_data()
    data.pop("departure_time")
    data.pop("arrival_deadline")
    result = evaluate(data)
    assert result.status == "feasible"
    assert result.timing_mode == "relative"
    assert len(result.legs) == 3
    assert result.totals.elapsed_s == 8400
    assert result.evaluated_departure_at is None
    assert all(stop.arrival_at is None and stop.departure_at is None
               and stop.physical_arrival_at is None for stop in result.stops)
    assert all(leg.departure_at is None and leg.arrival_at is None for leg in result.legs)
    assert "2000-01" not in result.model_dump_json()
    assert evaluate(data) == result


@pytest.mark.parametrize("constraint", ["deadline", "window"])
def test_absolute_time_constraints_need_departure_only_when_requested(constraint):
    data = request_data()
    data.pop("departure_time")
    if constraint == "window":
        data.pop("arrival_deadline")
        data["stops"][1]["earliest_arrival_at"] = "2026-10-24T10:00:00+08:00"
    with pytest.raises(ValidationError, match="明确出发时间"):
        MultiStopRequest.model_validate(data)
