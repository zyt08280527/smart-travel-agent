"""Fetch only adjacent route snapshots, then select whole fixed-order journeys."""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from travel_agent.domain.decision import TravelMode, TravelOption
from travel_agent.domain.multi_stop import (
    LegCandidates,
    MultiStopLeg,
    MultiStopPlanningResult,
    MultiStopRequest,
)
from travel_agent.domain.route import RoutePlan
from travel_agent.domain.transit import TransitPlan
from travel_agent.services.amap_driving import AmapDrivingRouteService
from travel_agent.services.amap_walking import AmapWalkingRouteService
from travel_agent.services.multi_stop_selection import (
    MultiStopSearchBudgetError,
    select_multi_stop_plans,
)
from travel_agent.services.transit import TransitService
from travel_agent.services.transit_selection import normalize_transit_candidate
from travel_agent.services.travel_planning import TRANSIT_STRATEGY_CODES


class MultiStopPlanningService:
    """Provider calls are bounded; one failed mode must not erase other real candidates."""

    def __init__(
        self, *, driving_service: AmapDrivingRouteService | None = None,
        walking_service: AmapWalkingRouteService | None = None,
        transit_service: TransitService | None = None,
        clock: Callable[[], datetime] | None = None,
        concurrency: int = 3, timeout_seconds: float = 30,
    ) -> None:
        if not 1 <= concurrency <= 12 or not 0 < timeout_seconds <= 120:
            raise ValueError("路线并发或超时设置超出允许范围")
        self._driving = driving_service or AmapDrivingRouteService()
        self._walking = walking_service or AmapWalkingRouteService()
        self._transit = transit_service or TransitService()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._concurrency = concurrency
        self._timeout = timeout_seconds

    async def plan(
        self, request: MultiStopRequest, *, max_route_calls: int = 12,
        transit_alternatives: int = 3, limit: int = 3,
    ) -> MultiStopPlanningResult:
        request = MultiStopRequest.model_validate(request.model_dump())
        if not 1 <= max_route_calls <= 12 or not 1 <= transit_alternatives <= 6:
            raise ValueError("路线调用或公共交通候选预算超出允许范围")
        if not 1 <= limit <= 10:
            raise ValueError("返回方案数量必须为 1 到 10")
        modes: list[TravelMode] = ["walking", "transit"]
        if request.preferences.can_drive is not False:
            modes.append("driving")
        calls = (len(request.stops) - 1) * len(modes)
        if calls > max_route_calls:
            raise MultiStopSearchBudgetError("相邻分段路线调用超过预算，未请求外部服务")
        semaphore = asyncio.Semaphore(self._concurrency)
        batches = await asyncio.gather(*[
            self._segment(request, index, modes, transit_alternatives, semaphore)
            for index in range(len(request.stops) - 1)
        ])
        selection = await asyncio.to_thread(select_multi_stop_plans, request, batches, limit=limit)
        selection.limitations.extend([
            "路线来自查询时快照，未按推演后的每段出发时刻重新请求未来路况或班次。",
        ])
        return MultiStopPlanningResult(
            request=request, segments=batches, selection=selection, route_service_calls=calls,
        )

    async def _segment(
        self, request: MultiStopRequest, index: int, modes: list[TravelMode],
        alternatives: int, semaphore: asyncio.Semaphore,
    ) -> LegCandidates:
        origin, destination = request.stops[index:index + 2]
        strategy = TRANSIT_STRATEGY_CODES[request.preferences.transit_strategy]
        candidates = []
        calls = {
            "walking": lambda: self._walking.plan_walking_route(origin.point, destination.point),
            "driving": lambda: self._driving.plan_driving_route(origin.point, destination.point),
            "transit": lambda: self._transit.plan_transit_route(
                origin.point, destination.point, strategy=strategy, alternative_routes=alternatives,
            ),
        }
        outcomes = await asyncio.gather(*[
            self._fetch(mode, calls[mode], semaphore) for mode in modes
        ])
        for mode, (result, failure, fetched_at) in zip(modes, outcomes, strict=True):
            common = {
                "origin_stop_id": origin.stop_id, "destination_stop_id": destination.stop_id,
                "source": "amap", "fetched_at": fetched_at,
            }
            if failure:
                candidates.append(MultiStopLeg(
                    **common, candidate_id=f"{mode}:failed",
                    option=TravelOption(mode=mode, status="failed", failure_reason=failure),
                ))
            elif isinstance(result, TransitPlan):
                for candidate_index, option in enumerate(result.options):
                    candidates.append(MultiStopLeg(
                        **common, candidate_id=f"transit:{candidate_index}",
                        option=normalize_transit_candidate(option, attribution=result.attribution),
                        transit_details=option,
                    ))
            else:
                walking = mode == "walking"
                candidates.append(MultiStopLeg(
                    **common, candidate_id=f"{mode}:0", route_details=result,
                    option=TravelOption(
                        mode=mode, distance_m=result.distance_m, duration_s=result.duration_s,
                        duration_basis=result.duration_basis,
                        walking_distance_m=result.distance_m if walking else None,
                        transfer_count=0, cost_yuan=0 if walking else None,
                        tolls_yuan=result.tolls_yuan, taxi_cost_yuan=result.taxi_cost_yuan,
                        traffic_status_counts=dict(Counter([
                            segment.status for segment in result.traffic_segments
                        ])),
                        geometry=result.geometry, attribution=result.attribution,
                    ),
                ))
        return LegCandidates(origin_stop_id=origin.stop_id, destination_stop_id=destination.stop_id,
                             candidates=candidates)

    async def _fetch(
        self, mode: TravelMode, call: Callable[[], Awaitable[RoutePlan | TransitPlan]],
        semaphore: asyncio.Semaphore,
    ) -> tuple[RoutePlan | TransitPlan | None, str | None, datetime]:
        result, failure = None, None
        async with semaphore:
            try:
                result = await asyncio.wait_for(call(), timeout=self._timeout)
                if ((mode == "transit" and not isinstance(result, TransitPlan))
                        or (mode != "transit" and (
                            not isinstance(result, RoutePlan) or result.mode != mode))):
                    result, failure = None, "路线响应格式不匹配，请稍后重试。"
                elif isinstance(result, TransitPlan):
                    result = TransitPlan.model_validate(result.model_dump())
                elif isinstance(result, RoutePlan):
                    result = RoutePlan.model_validate(result.model_dump())
            except TimeoutError:
                failure = "路线查询超时，请稍后重试。"
            except Exception:
                # Provider exceptions may contain request URLs/keys; never expose their text.
                failure = "路线查询失败，请稍后重试。"
        return result, failure, self._clock()
