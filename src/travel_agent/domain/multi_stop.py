"""Fixed-order, same-city multi-stop inputs and deterministic timeline output."""

from datetime import UTC, datetime
from math import isfinite
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from travel_agent.domain.decision import TravelOption, TravelPreferences
from travel_agent.domain.journey_time import ArrivalDeadline, DepartureTime
from travel_agent.domain.route import GeoPoint, RoutePlan
from travel_agent.domain.transit import TransitOption


class MultiStopModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class JourneyStop(MultiStopModel):
    """A visit occurrence; repeated visits to one POI have different stop IDs."""

    stop_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    city_code: str = Field(min_length=1, max_length=20)
    point: GeoPoint
    provider_place_id: str | None = None
    stay_minutes: float = Field(default=0, ge=0, le=1440)
    earliest_arrival_at: AwareDatetime | None = None
    latest_arrival_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered_window(self) -> "JourneyStop":
        if (self.earliest_arrival_at is not None and self.latest_arrival_at is not None
                and self.earliest_arrival_at.astimezone(UTC)
                > self.latest_arrival_at.astimezone(UTC)):
            raise ValueError("最早到达时间不能晚于最晚到达时间")
        return self


class MultiStopRequest(MultiStopModel):
    """One origin, 1–3 intermediate visits, one destination; never reorder."""

    schema_version: Literal[2] = 2
    city_code: str = Field(min_length=1, max_length=20)
    city_name: str | None = Field(default=None, max_length=100)
    stops: list[JourneyStop] = Field(min_length=3, max_length=5)
    departure_time: DepartureTime | None = None
    arrival_deadline: ArrivalDeadline | None = None
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    max_transport_cost_yuan: float | None = Field(default=None, ge=0, le=100000)
    max_elapsed_minutes: float | None = Field(default=None, gt=0, le=10080)

    @model_validator(mode="after")
    def validate_scope(self) -> "MultiStopRequest":
        if len({stop.stop_id for stop in self.stops}) != len(self.stops):
            raise ValueError("每次到访必须有独立且唯一的 stop_id")
        if any(stop.city_code != self.city_code for stop in self.stops):
            raise ValueError("首版仅支持同城行程，地点须使用统一城市编码")
        origin, destination = self.stops[0], self.stops[-1]
        if origin.stay_minutes or origin.earliest_arrival_at or origin.latest_arrival_at:
            raise ValueError("起点使用明确的出发时间，不设置停留或到达时间窗")
        if destination.stay_minutes:
            raise ValueError("终点到达即结束，停留时间仅用于中间点")
        if self.departure_time is None and (self.arrival_deadline or any(
            stop.earliest_arrival_at or stop.latest_arrival_at for stop in self.stops
        )):
            raise ValueError("校验到达时间窗或截止时间时，请提供明确出发时间")
        if (self.departure_time is not None
                and self.departure_time.precision not in {"now", "exact"}):
            raise ValueError("时间推演需要明确出发时刻，请先澄清日期或时段")
        walking_limit = self.preferences.max_walking_distance_m
        if walking_limit is not None and not isfinite(walking_limit):
            raise ValueError("最大步行距离必须是有限数值")
        return self


class MultiStopLeg(MultiStopModel):
    """One chosen adjacent route; providers/combination selection are separate."""

    origin_stop_id: str = Field(min_length=1, max_length=100)
    destination_stop_id: str = Field(min_length=1, max_length=100)
    candidate_id: str = Field(min_length=1, max_length=100)
    option: TravelOption
    source: str = Field(min_length=1, max_length=200)
    fetched_at: AwareDatetime
    limitations: list[str] = Field(default_factory=list)
    route_details: RoutePlan | None = None
    transit_details: TransitOption | None = None


class StopTiming(MultiStopModel):
    stop_index: int = Field(ge=0)
    stop_id: str
    name: str
    physical_arrival_at: AwareDatetime | None
    arrival_at: AwareDatetime | None
    departure_at: AwareDatetime | None
    waiting_s: float = Field(ge=0)
    stay_s: float = Field(ge=0)


class LegTiming(MultiStopModel):
    leg_index: int = Field(ge=0)
    leg: MultiStopLeg
    departure_at: AwareDatetime | None
    arrival_at: AwareDatetime | None


class TimelineIssue(MultiStopModel):
    code: Literal[
        "route_unavailable", "arrival_window", "arrival_deadline", "driving_not_allowed",
        "driving_continuity", "walking_limit", "transfer_limit", "walking_unknown",
        "transfers_unknown",
        "cost_limit", "cost_unknown",
        "duration_limit",
    ]
    message: str
    stop_index: int | None = Field(default=None, ge=0)
    delay_s: float | None = Field(default=None, ge=0)
    missing_data: bool = False


class JourneyTotals(MultiStopModel):
    travel_s: float = Field(ge=0)
    waiting_s: float = Field(ge=0)
    stay_s: float = Field(ge=0)
    elapsed_s: float = Field(ge=0)
    distance_m: float = Field(ge=0)
    walking_distance_m: float | None = Field(default=None, ge=0)
    transfer_count: int | None = Field(default=None, ge=0)
    known_cost_subtotal_yuan: float = Field(ge=0)
    total_cost_yuan: float | None = Field(default=None, ge=0)


class MultiStopEvaluation(MultiStopModel):
    timing_mode: Literal["scheduled", "relative"] = "scheduled"
    status: Literal["feasible", "infeasible", "insufficient_data"]
    stops: list[StopTiming]
    legs: list[LegTiming]
    totals: JourneyTotals | None = None
    issues: list[TimelineIssue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evaluated_departure_at: datetime | None


class LegCandidates(MultiStopModel):
    origin_stop_id: str = Field(min_length=1, max_length=100)
    destination_stop_id: str = Field(min_length=1, max_length=100)
    candidates: list[MultiStopLeg] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_candidates(self) -> "LegCandidates":
        if len({leg.candidate_id for leg in self.candidates}) != len(self.candidates):
            raise ValueError("同一分段候选 ID 不能重复")
        if any(leg.origin_stop_id != self.origin_stop_id
               or leg.destination_stop_id != self.destination_stop_id for leg in self.candidates):
            raise ValueError("候选路线必须属于当前相邻分段")
        return self


class MultiStopScores(MultiStopModel):
    time: float = Field(ge=0, le=100)
    cost: float = Field(ge=0, le=100)
    walking: float = Field(ge=0, le=100)
    transfers: float = Field(ge=0, le=100)
    total: float = Field(ge=0, le=100)


class RankedMultiStopPlan(MultiStopModel):
    plan_id: str
    evaluation: MultiStopEvaluation
    scores: MultiStopScores
    subway_leg_count: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class MultiStopSelection(MultiStopModel):
    status: Literal["feasible", "infeasible", "insufficient_data"]
    recommended_plan_id: str | None = None
    selected_plan_id: str | None = None
    plans: list[RankedMultiStopPlan] = Field(default_factory=list)
    evaluated_combinations: int = Field(ge=0)
    feasible_combinations: int = Field(ge=0)
    rejected_combinations: int = Field(ge=0)
    insufficient_combinations: int = Field(ge=0)
    issue_counts: dict[str, int] = Field(default_factory=dict)
    issue_examples: list[TimelineIssue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MultiStopPlanningResult(MultiStopModel):
    request: MultiStopRequest
    segments: list[LegCandidates]
    selection: MultiStopSelection
    route_service_calls: int = Field(ge=0)


class MultiStopDraft(MultiStopModel):
    draft_id: UUID
    result: MultiStopPlanningResult
    created_at: AwareDatetime


class SavedMultiStopJourney(MultiStopModel):
    itinerary_id: UUID
    draft: MultiStopDraft
    selected_plan_id: str
    saved_at: AwareDatetime


class SaveMultiStopRequest(MultiStopModel):
    selected_plan_id: str = Field(min_length=1, max_length=100)


class SelectMultiStopRequest(MultiStopModel):
    candidate_ids: list[str] = Field(min_length=2, max_length=4)
