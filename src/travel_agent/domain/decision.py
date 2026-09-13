from typing import Literal

from pydantic import BaseModel, Field, model_validator

from travel_agent.domain.journey_time import DepartureTime
from travel_agent.domain.weather import WeatherData

TravelMode = Literal["walking", "transit", "driving"]
TravelPriority = Literal[
    "balanced",
    "fastest",
    "cheapest",
    "least_walking",
    "fewest_transfers",
]
OptionStatus = Literal["available", "unavailable", "failed"]


class TravelPreferences(BaseModel):
    """User constraints and priorities that can change a route recommendation."""

    priority: TravelPriority = "balanced"
    can_drive: bool | None = None
    max_walking_distance_m: float | None = Field(default=None, ge=0)
    max_transfer_count: int | None = Field(default=None, ge=0)


class JourneyContext(BaseModel):
    """Resolved facts needed to compare travel options for one journey."""

    city: str = Field(min_length=1)
    origin_name: str = Field(min_length=1)
    destination_name: str = Field(min_length=1)
    departure_time: DepartureTime | None = None
    weather: WeatherData | None = None
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)


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


class TravelComparisonResult(BaseModel):
    """Weather context plus the ranked result of a multi-mode comparison."""

    context: JourneyContext
    recommendation: TravelRecommendation
