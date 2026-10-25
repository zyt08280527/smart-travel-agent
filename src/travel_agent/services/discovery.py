"""Small deterministic round-trip discovery pilot; no invented venues or facts."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from travel_agent.domain.discovery import (
    DiscoveryCandidate,
    DiscoveryExclusion,
    DiscoveryRequest,
    DiscoveryResult,
)
from travel_agent.domain.multi_stop import MultiStopDraft, MultiStopRequest, RankedMultiStopPlan
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport, StopWeather
from travel_agent.domain.place import Place
from travel_agent.services.attraction import AttractionService, load_profiles
from travel_agent.services.multi_stop_planning import MultiStopPlanningService
from travel_agent.services.multi_stop_weather import MultiStopWeatherService


class DiscoveryService:
    def __init__(
        self, planner: MultiStopPlanningService | None = None,
        weather_service: MultiStopWeatherService | None = None,
    ):
        self._planner = planner or MultiStopPlanningService()
        self._weather = weather_service or MultiStopWeatherService()

    async def recommend(
        self, request: DiscoveryRequest, *, now: datetime | None = None,
    ) -> DiscoveryResult:
        current = now or datetime.now(UTC)
        if request.departure_at is not None and request.departure_at < current:
            raise ValueError("请选择未来的出发时间")
        profiles = sorted(
            [p for p in load_profiles() if p.enabled and p.card.city_code == "440300"],
            key=lambda p: p.card.attraction_id,
        )
        if len(profiles) > 3:
            raise ValueError("当前试点最多核算三个馆区，请先调整候选调用预算")
        if not set(request.excluded_attraction_ids) <= {p.card.attraction_id for p in profiles}:
            raise ValueError("排除的目的地不属于当前馆区范围")
        service = AttractionService(profiles)
        candidates, excluded = [], []
        for profile in profiles:
            def reject(
                reason: str, identity=profile.card.attraction_id, name=profile.card.name,
            ) -> None:
                excluded.append(DiscoveryExclusion(
                    attraction_id=identity, name=name, reason=reason,
                ))

            if profile.card.attraction_id in request.excluded_attraction_ids:
                reject("用户已明确排除此目的地；未自动恢复或放宽条件。")
                continue

            # This pilot has no date-specific open/closed facts. A weekly text
            # schedule is not proof of opening on the requested date.
            if request.require_confirmed_open:
                reject("缺少指定参观日期的开放核验，无法满足确保开放要求。")
                continue
            place = Place(
                display_name=profile.card.name, latitude=profile.latitude,
                longitude=profile.longitude, city_code=profile.card.city_code,
                provider_place_id=profile.provider_place_ids[0],
            )
            card = service.lookup(place, now=max(current, request.departure_at or current)).card
            if card is None:
                reject("景点实体绑定不唯一或无效，待维护者核实。")
                continue
            ticket = next(fact for fact in card.facts if fact.kind == "ticket")
            price = ticket.ticket_price_yuan if ticket.status == "verified" else None
            if request.budget_yuan is not None:
                if price is None:
                    reject("门票费用未知或超出复核期限，无法确认满足交通与门票预算。")
                    continue
                if price > request.budget_yuan:
                    reject("已核验门票费用超过预算。")
                    continue
            deadline = (request.departure_at + timedelta(minutes=request.available_minutes)
                        if request.departure_at else None)
            stops = []
            for identity, item, stay in [
                ("origin", request.origin, 0), ("visit", place, request.stay_minutes),
                ("return", request.origin, 0),
            ]:
                stops.append({
                    "stop_id": identity, "name": item.display_name, "city_code": "440300",
                    "point": {"latitude": item.latitude, "longitude": item.longitude},
                    "provider_place_id": item.provider_place_id, "stay_minutes": stay,
                })
            plan_request = MultiStopRequest.model_validate({
                "city_code": "440300", "city_name": "深圳", "stops": stops,
                "departure_time": {"departure_at": request.departure_at,
                                   "precision": "exact", "source_text": "目的地推荐出发时间"}
                                  if request.departure_at else None,
                "arrival_deadline": {"arrival_by": deadline, "buffer_minutes": 0,
                                     "precision": "exact", "source_text": "往返可用时间截止"}
                                    if deadline else None,
                "max_elapsed_minutes": request.available_minutes,
                "preferences": request.preferences.model_dump(),
                "max_transport_cost_yuan": (
                    request.budget_yuan - price if request.budget_yuan is not None else None
                ),
            })
            try:
                result = await asyncio.wait_for(self._planner.plan(plan_request), timeout=75)
            except (TimeoutError, ValueError, RuntimeError):
                reject("路线核算暂时失败，不能据此判断现实中没有路线。")
                continue
            if not result.selection.plans:
                reject("本次路线候选无法确认满足往返时间、费用或出行限制。")
                continue
            selected = next(
                plan for plan in result.selection.plans
                if plan.plan_id == result.selection.recommended_plan_id
            )
            totals = selected.evaluation.totals
            cost = totals.total_cost_yuan
            weather = None
            if request.weather_policy != "none":
                # This temporary in-memory draft never enters the workspace DB.
                draft = MultiStopDraft(draft_id=uuid4(), result=result, created_at=current)
                weather = await self._weather_report(draft, selected, now=now)
                if request.weather_policy == "exclude_signals":
                    if any(stop.status != "forecast" for stop in weather.stops):
                        reject("本次推荐路线存在天气未知的到达采样点，未通过所选天气筛选。")
                        continue
                    if any(stop.warnings for stop in weather.stops):
                        reject("本次推荐路线的到达采样点有天气提示，未通过所选天气筛选；"
                               "未比较其他路线的天气。")
                        continue
            candidates.append(DiscoveryCandidate(
                card=card, request=plan_request, totals=totals,
                weather=weather,
                route_sources=sorted({
                    item.leg.option.attribution for item in selected.evaluation.legs
                    if item.leg.option.attribution
                }),
                route_fetched_at=max(item.leg.fetched_at for item in selected.evaluation.legs),
                estimated_transport_and_ticket_yuan=(
                    cost + price if cost is not None and price is not None else None
                ),
                reasons=[
                    f"按停留 {request.stay_minutes} 分钟核算，"
                    f"往返预计 {totals.elapsed_s / 60:g} 分钟。",
                    "路线候选满足本次明确的时间、步行、换乘及交通预算限制。",
                ],
                warnings=["指定日期开放与临时安排待确认；路线可行不代表届时可参观。"]
                + (["交通与普通门票合计未知，不按免费处理。"]
                   if cost is None or price is None else [])
                + (["天气只对应本次推荐路线的到达采样点；进入正式规划后请刷新天气核对。"]
                   if weather is not None else [])
                + (["部分到达采样点天气未知，请展开天气详情核对。"]
                   if weather and any(stop.status != "forecast" for stop in weather.stops) else [])
                + (["到达采样点触发了天气提示，请展开详情查看并自行决定是否继续。"]
                   if weather and any(stop.warnings for stop in weather.stops) else []),
            ))
        # Each venue uses the existing deterministic route preference; across
        # venues use elapsed time, then ID for stable ordering. No fake diversity.
        candidates.sort(key=lambda item: (item.totals.elapsed_s, item.card.attraction_id))
        return DiscoveryResult(candidates=candidates, excluded=excluded, limitations=[
            "仅比较现有三个深圳博物馆馆区，不代表全市景点或多样化游玩推荐。",
            "每馆区路线先按所选出行偏好筛选，馆区之间按往返预计耗时排序。",
            "费用仅含本次已知交通与普通门票，不含餐饮、停车、购物及其他服务。",
            ("本次未启用天气查询。" if request.weather_policy == "none" else
             "天气筛选仅检查本次推荐路线的到达采样点，不覆盖整段路线或全部停留，"
             "不进行天气路线重排或保证全程安全。"),
            "同行者适配和指定日期开放核验尚未接入；没有保证可预约或营业。",
            "路线为查询时估计；进入正式规划后会重新查询，预计时间与费用可能变化。",
        ])

    async def _weather_report(
        self, draft: MultiStopDraft, selected: RankedMultiStopPlan, *, now: datetime | None,
    ) -> MultiStopWeatherReport:
        current = now or datetime.now(UTC)
        try:
            report = await asyncio.wait_for(
                self._weather.report(draft, selected.plan_id, now=current), timeout=26,
            )
            expected = [(stop.stop_id, stop.arrival_at) for stop in selected.evaluation.stops]
            actual = [(stop.stop_id, stop.target_at) for stop in report.stops]
            if report.plan_id != selected.plan_id or actual != expected:
                raise ValueError("天气报告与本次推荐路线不符")
            return report
        except (TimeoutError, ValueError, RuntimeError, httpx.HTTPError):
            # Never turn missing weather into a favorable score or safety claim.
            return MultiStopWeatherReport(
                plan_id=selected.plan_id, queried_at=current,
                stops=[StopWeather(
                    stop_id=stop.stop_id, name=stop.name, target_at=stop.arrival_at,
                    status="unavailable", message="天气补充暂时不可用，不能视为天气适宜。",
                ) for stop in selected.evaluation.stops],
                limitations=["天气失败不影响原路线核算；启用排除模式时未知不通过。"],
            )
