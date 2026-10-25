"""Evaluate one fixed sequence of selected routes, without network calls or ranking."""

from datetime import UTC, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from travel_agent.domain.journey_time import DepartureTime
from travel_agent.domain.multi_stop import (
    JourneyTotals,
    LegTiming,
    MultiStopEvaluation,
    MultiStopLeg,
    MultiStopRequest,
    StopTiming,
    TimelineIssue,
)


def evaluate_fixed_order(
    request: MultiStopRequest, legs: list[MultiStopLeg],
) -> MultiStopEvaluation:
    request = MultiStopRequest.model_validate(request.model_dump())
    if request.departure_time is not None:
        return _evaluate_fixed_order(request, legs)
    # A private arithmetic anchor lets the same constraint logic calculate durations.
    # It is never returned as a user departure or used to request a forecast.
    anchored = request.model_copy(update={"departure_time": DepartureTime(
        departure_at=datetime(2000, 1, 1, tzinfo=UTC), timezone="Asia/Shanghai",
        precision="exact", source_text="内部相对耗时计算基准",
    )})
    result = _evaluate_fixed_order(anchored, legs)
    result.timing_mode = "relative"
    result.evaluated_departure_at = None
    for stop in result.stops:
        stop.physical_arrival_at = stop.arrival_at = stop.departure_at = None
    for leg in result.legs:
        leg.departure_at = leg.arrival_at = None
    result.limitations.append("未指定出发时间：仅估算分段、停留与全程耗时，不校验到达时刻或截止；公交为当前查询快照，不保证实际出发时班次。")
    return result


def _evaluate_fixed_order(
    request: MultiStopRequest, legs: list[MultiStopLeg],
) -> MultiStopEvaluation:
    """Wait before early visits, retain late evidence, and never invent missing routes."""
    # Revalidate mutable caller objects and hold an isolated snapshot for this calculation.
    request = MultiStopRequest.model_validate(request.model_dump())
    legs = [MultiStopLeg.model_validate(leg.model_dump()) for leg in legs]
    if len(legs) != len(request.stops) - 1:
        raise ValueError("分段数必须等于地点数减一")
    for index, leg in enumerate(legs):
        if (leg.origin_stop_id != request.stops[index].stop_id
                or leg.destination_stop_id != request.stops[index + 1].stop_id):
            raise ValueError("每段路线必须连接用户指定顺序的相邻地点")
        # TravelOption predates these models and permits non-finite optional numbers.
        for field in ("duration_s", "distance_m", "walking_distance_m", "cost_yuan"):
            value = getattr(leg.option, field)
            if value is not None and not isfinite(value):
                raise ValueError(f"路线 {field} 必须是有限数值")

    zone = ZoneInfo(request.departure_time.timezone)
    start = request.departure_time.departure_at.astimezone(UTC)
    current = start
    origin = request.stops[0]
    timings = [StopTiming(
        stop_index=0, stop_id=origin.stop_id, name=origin.name,
        physical_arrival_at=start.astimezone(zone), arrival_at=start.astimezone(zone),
        departure_at=start.astimezone(zone), waiting_s=0, stay_s=0,
    )]
    scheduled_legs = []
    issues = []
    limitations = [
        "时间线按给定顺序和所选分段推演，不调整地点访问顺序。",
        "路线耗时沿用输入快照，不能保证未来路况、公交班次或准点。",
    ]
    modes = [leg.option.mode for leg in legs]
    if "driving" in modes and any(mode != "driving" for mode in modes):
        issues.append(TimelineIssue(
            code="driving_continuity", message="首版驾车行程要求每段均驾车，暂不支持停车换乘。",
        ))
    if request.preferences.can_drive is False and "driving" in modes:
        issues.append(TimelineIssue(code="driving_not_allowed", message="出行限制为不能驾车。"))

    for index, leg in enumerate(legs):
        limitations.extend(leg.limitations)
        option = leg.option
        if option.status != "available":
            issues.append(TimelineIssue(
                code="route_unavailable", stop_index=index + 1, missing_data=True,
                message=f"第 {index + 1} 段路线不可用：{option.failure_reason}，后续时间无法推演。",
            ))
            return MultiStopEvaluation(
                status=_status(issues), stops=timings, legs=scheduled_legs, issues=issues,
                limitations=list(dict.fromkeys(limitations)), evaluated_departure_at=start,
            )
        physical_arrival = current + timedelta(seconds=option.duration_s)
        scheduled_legs.append(LegTiming(
            leg_index=index, leg=leg, departure_at=current.astimezone(zone),
            arrival_at=physical_arrival.astimezone(zone),
        ))
        stop = request.stops[index + 1]
        arrival = physical_arrival
        if stop.earliest_arrival_at is not None:
            arrival = max(arrival, stop.earliest_arrival_at.astimezone(UTC))
        if stop.latest_arrival_at is not None:
            late_s = (arrival - stop.latest_arrival_at.astimezone(UTC)).total_seconds()
            if late_s > 0:
                issues.append(TimelineIssue(
                    code="arrival_window", stop_index=index + 1, delay_s=late_s,
                    message=(f"第 {index + 2} 站“{stop.name}”超出最晚到达时间"
                             f" {late_s / 60:g} 分钟。"),
                ))
        stay_s = stop.stay_minutes * 60
        current = arrival + timedelta(seconds=stay_s)
        timings.append(StopTiming(
            stop_index=index + 1, stop_id=stop.stop_id, name=stop.name,
            physical_arrival_at=physical_arrival.astimezone(zone),
            arrival_at=arrival.astimezone(zone), departure_at=current.astimezone(zone),
            waiting_s=(arrival - physical_arrival).total_seconds(), stay_s=stay_s,
        ))

    if request.arrival_deadline is not None:
        deadline = request.arrival_deadline.arrival_by.astimezone(UTC) - timedelta(
            minutes=request.arrival_deadline.buffer_minutes,
        )
        late_s = (current - deadline).total_seconds()
        if late_s > 0:
            issues.append(TimelineIssue(
                code="arrival_deadline", stop_index=len(request.stops) - 1, delay_s=late_s,
                message=f"终点到达比保留缓冲后的截止时间晚 {late_s / 60:g} 分钟。",
            ))

    totals = _totals(legs, timings)
    if (request.max_elapsed_minutes is not None
            and totals.elapsed_s > request.max_elapsed_minutes * 60):
        issues.append(TimelineIssue(
            code="duration_limit", message="全程交通、等待与停留耗时超过总可用时长。",
        ))
    prefs = request.preferences
    for value, limit, unknown, exceeded, label in (
        (totals.walking_distance_m, prefs.max_walking_distance_m,
         "walking_unknown", "walking_limit", "总步行距离"),
        (totals.transfer_count, prefs.max_transfer_count,
         "transfers_unknown", "transfer_limit", "总换乘次数"),
        (totals.total_cost_yuan, request.max_transport_cost_yuan,
         "cost_unknown", "cost_limit", "总交通费用"),
    ):
        if limit is None:
            continue
        if value is None:
            issues.append(TimelineIssue(code=unknown, missing_data=True,
                                        message=f"{label}缺失，无法确认满足限制。"))
        elif value > limit:
            issues.append(TimelineIssue(
                code=exceeded, message=f"{label} {value:g} 超过限制 {limit:g}。",
            ))
    if totals.total_cost_yuan is None:
        limitations.append("部分分段费用未知，已知费用小计不是全程总费用。")
    return MultiStopEvaluation(
        status=_status(issues), stops=timings, legs=scheduled_legs, totals=totals,
        issues=issues, limitations=list(dict.fromkeys(limitations)), evaluated_departure_at=start,
    )


def _status(issues: list[TimelineIssue]) -> str:
    if any(not issue.missing_data for issue in issues):
        return "infeasible"
    return "insufficient_data" if issues else "feasible"


def _totals(legs: list[MultiStopLeg], timings: list[StopTiming]) -> JourneyTotals:
    options = [leg.option for leg in legs]
    walking = [option.distance_m if option.mode == "walking" else option.walking_distance_m
               for option in options]
    transfers = [option.transfer_count if option.mode == "transit" else 0 for option in options]
    costs = [option.cost_yuan for option in options]
    travel_s = sum(option.duration_s for option in options)
    waiting_s = sum(stop.waiting_s for stop in timings)
    stay_s = sum(stop.stay_s for stop in timings)
    return JourneyTotals(
        travel_s=travel_s, waiting_s=waiting_s, stay_s=stay_s,
        elapsed_s=travel_s + waiting_s + stay_s,
        distance_m=sum(option.distance_m for option in options),
        walking_distance_m=None if None in walking else sum(walking),
        transfer_count=None if None in transfers else sum(transfers),
        known_cost_subtotal_yuan=sum(cost for cost in costs if cost is not None),
        total_cost_yuan=None if None in costs else sum(costs),
    )
