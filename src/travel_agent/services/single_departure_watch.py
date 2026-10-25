"""Refresh only the saved single-leg mode using trusted saved coordinates."""

from datetime import timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict

from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.services.amap_driving import AmapDrivingRouteService
from travel_agent.services.amap_walking import AmapWalkingRouteService
from travel_agent.services.transit import TransitService
from travel_agent.services.travel_planning import TRANSIT_STRATEGY_CODES


class SingleWatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    departure_at: AwareDatetime | None = None


class SingleWatchSnapshot(BaseModel):
    kind: str = "single"
    saved: SavedItinerary
    departure_at: AwareDatetime


class SingleDepartureChecker:
    def __init__(self, weather, *, driving=None, walking=None, transit=None):
        self.weather = weather
        self.driving = driving
        self.walking = walking
        self.transit = transit

    async def check(self, snapshot: SingleWatchSnapshot, now):
        saved = snapshot.saved
        context = saved.watch_context
        if not context or not context.origin or not context.destination:
            raise ValueError("缺少可信坐标")
        origin, destination = context.origin, context.destination
        prefs = context.preferences
        mode = saved.travel_mode
        if mode == "transit":
            result = await (self.transit or TransitService()).plan_transit_route(
                origin,
                destination,
                strategy=TRANSIT_STRATEGY_CODES[prefs.transit_strategy],
            )
            if result.origin != origin or result.destination != destination:
                raise ValueError("公共交通起终点不匹配")
            candidates = [
                o
                for o in result.options
                if (
                    prefs.max_walking_distance_m is None
                    or o.walking_distance_m <= prefs.max_walking_distance_m
                )
                and (
                    prefs.max_transfer_count is None or o.transfer_count <= prefs.max_transfer_count
                )
            ]
            if not candidates:
                raise ValueError("没有满足原条件的公共交通候选")
            duration = min(o.duration_s for o in candidates)
        else:
            if mode == "driving":
                if prefs.can_drive is False:
                    raise ValueError("原条件不允许驾车")
                result = await (self.driving or AmapDrivingRouteService()).plan_driving_route(
                    origin,
                    destination,
                )
            else:
                result = await (self.walking or AmapWalkingRouteService()).plan_walking_route(
                    origin,
                    destination,
                )
                if (
                    prefs.max_walking_distance_m is not None
                    and result.distance_m > prefs.max_walking_distance_m
                ):
                    raise ValueError("新步行路线超过原限制")
            if result.origin != origin or result.destination != destination or result.mode != mode:
                raise ValueError("供应商路线不匹配")
            duration = result.duration_s
        arrival = snapshot.departure_at + timedelta(seconds=duration)
        points = [
            ("origin", saved.origin, origin, snapshot.departure_at),
            ("destination", saved.destination, destination, arrival),
        ]
        report = await self.weather.report_points(str(saved.itinerary_id), points, now=now)
        if (
            report.plan_id != saved.itinerary_id
            or len(report.stops) != 2
            or {s.stop_id: s.target_at for s in report.stops} != {p[0]: p[3] for p in points}
            or any(s.status != "forecast" for s in report.stops)
        ):
            raise ValueError("部分天气未知")
        notes = [f"{s.name}：{w}" for s in report.stops for w in s.warnings]
        delta = duration - saved.duration_s
        if delta >= max(600, saved.duration_s * 0.2):
            notes.insert(0, f"同方式新估计比保存方案增加约{round(delta / 60)}分钟")
        deadline = context.arrival_deadline
        if deadline and arrival > deadline.arrival_by - timedelta(minutes=deadline.buffer_minutes):
            notes.append("新估计不能满足原到达截止及缓冲，请核对并考虑重新规划")
        return ("；".join(notes) if notes else "本次采样未发现达到提示阈值的变化") + (
            "。路线来源：高德；天气来源：Open-Meteo（起终点采样）。"
            "同方式候选不保证是原公交线路或班次；不是全程天气或准点保证。"
            "请自行决定是否重新规划；原保存行程未改变。"
        )
