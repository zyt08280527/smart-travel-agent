from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from travel_agent.domain.journey_time import ArrivalDeadline, DepartureTime
from travel_agent.domain.route import GeoPoint
from travel_agent.domain.transit import TransitOption as TransitRouteOption
from travel_agent.domain.weather import WeatherData

TravelMode = Literal["walking", "transit", "driving"]
TravelPriority = Literal[
    "balanced",
    "fastest",
    "cheapest",
    "least_walking",
    "fewest_transfers",
]
TransitStrategy = Literal[
    "recommended",
    "subway_first",
    "fewest_transfers",
    "least_walking",
]
OptionStatus = Literal["available", "unavailable", "failed"]


class TravelPreferences(BaseModel):
    """User constraints and priorities that can change a route recommendation."""

    priority: TravelPriority = "balanced"
    transit_strategy: TransitStrategy = "recommended"
    can_drive: bool | None = None
    max_walking_distance_m: float | None = Field(default=None, ge=0)
    max_transfer_count: int | None = Field(default=None, ge=0)


class JourneyContext(BaseModel):
    """Resolved facts needed to compare travel options for one journey."""

    city: str = Field(min_length=1)
    origin_name: str = Field(min_length=1)
    destination_name: str = Field(min_length=1)
    origin: GeoPoint | None = None
    destination: GeoPoint | None = None
    route_snapshot_at: datetime | None = None
    route_snapshot_expires_at: datetime | None = None
    departure_time: DepartureTime | None = None
    arrival_deadline: ArrivalDeadline | None = None
    weather: WeatherData | None = None
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)

    @model_validator(mode="after")
    def validate_route_snapshot(self) -> "JourneyContext":
        for field_name in ("route_snapshot_at", "route_snapshot_expires_at"):
            value = getattr(self, field_name)
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise ValueError(f"{field_name} must include timezone information")
        if (
            self.route_snapshot_at is not None
            and self.route_snapshot_expires_at is not None
            and self.route_snapshot_expires_at < self.route_snapshot_at
        ):
            raise ValueError(
                "route_snapshot_expires_at cannot precede route_snapshot_at"
            )
        return self


class TravelOption(BaseModel):
    """One normalized route candidate or one explicitly unavailable mode."""

    mode: TravelMode
    status: OptionStatus = "available"
    distance_m: float | None = Field(default=None, ge=0)
    duration_s: float | None = Field(default=None, ge=0)
    duration_basis: Literal[
        "static_without_live_traffic",
        "traffic_aware_estimate",
    ] = "static_without_live_traffic"
    walking_distance_m: float | None = Field(default=None, ge=0)
    cost_yuan: float | None = Field(default=None, ge=0)
    tolls_yuan: float | None = Field(default=None, ge=0)
    taxi_cost_yuan: float | None = Field(default=None, ge=0)
    transfer_count: int | None = Field(default=None, ge=0)
    latest_departure_at: datetime | None = None
    traffic_status_counts: dict[str, int] = Field(default_factory=dict)
    attribution: str | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "TravelOption":
        """Prevent missing route facts or fake zero values from entering scoring."""
        if self.status == "available":
            if self.distance_m is None or self.duration_s is None:
                raise ValueError(
                    "available travel options require distance_m and duration_s"
                )
            if not self.attribution:
                raise ValueError("available travel options require attribution")
        elif not self.failure_reason:
            raise ValueError(
                "unavailable or failed travel options require failure_reason"
            )
        if self.latest_departure_at is not None and (
            self.latest_departure_at.tzinfo is None
            or self.latest_departure_at.utcoffset() is None
        ):
            raise ValueError("latest_departure_at must include timezone information")
        return self


class ScoreBreakdown(BaseModel):
    """Explainable component scores produced by the future decision engine."""

    time: float = Field(ge=0, le=100)
    cost: float = Field(ge=0, le=100)
    walking: float = Field(ge=0, le=100)
    transfers: float = Field(ge=0, le=100)
    weather_fit: float = Field(ge=0, le=100)
    total: float = Field(ge=0, le=100)


class ScoredTravelOption(BaseModel):
    """A successful candidate paired with deterministic scores and reasons."""

    option: TravelOption
    scores: ScoreBreakdown
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_available_option(self) -> "ScoredTravelOption":
        if self.option.status != "available":
            raise ValueError("only available travel options can be scored")
        return self


class TravelRecommendation(BaseModel):
    """Ranked and explainable output returned by the decision layer."""

    recommended_mode: TravelMode
    ranked_options: list[ScoredTravelOption] = Field(min_length=1)
    unavailable_options: list[TravelOption] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    summary_reasons: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def recommended_mode_must_rank_first(self) -> "TravelRecommendation":
        first_mode = self.ranked_options[0].option.mode
        if self.recommended_mode != first_mode:
            raise ValueError("recommended_mode must match the first ranked option")
        return self


class TravelRecommendationVariant(BaseModel):
    """One deterministic ranking generated from the same provider snapshot."""

    priority: TravelPriority
    recommended_mode: TravelMode
    ranked_options: list[ScoredTravelOption] = Field(min_length=1)

    @model_validator(mode="after")
    def recommended_mode_must_rank_first(self) -> "TravelRecommendationVariant":
        if self.recommended_mode != self.ranked_options[0].option.mode:
            raise ValueError("recommended_mode must match the first ranked option")
        return self


class TravelComparisonResult(BaseModel):
    """Weather context plus the ranked result of a multi-mode comparison."""

    context: JourneyContext
    recommendation: TravelRecommendation
    recommendation_variants: list[TravelRecommendationVariant] = Field(
        default_factory=list
    )
    transit_candidates: list[TransitRouteOption] = Field(default_factory=list)
    selected_transit_candidate_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def selected_transit_candidate_must_exist(self) -> "TravelComparisonResult":
        index = self.selected_transit_candidate_index
        if index is None:
            if self.transit_candidates:
                raise ValueError(
                    "selected_transit_candidate_index is required when candidates exist"
                )
            return self
        if index >= len(self.transit_candidates):
            raise ValueError(
                "selected_transit_candidate_index must reference a candidate"
            )
        return self
