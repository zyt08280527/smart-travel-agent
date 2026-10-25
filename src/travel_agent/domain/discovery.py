from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from travel_agent.domain.attraction import AttractionCard, AttractionModel
from travel_agent.domain.decision import TravelPreferences, TravelPriority
from travel_agent.domain.multi_stop import JourneyTotals, MultiStopDraft, MultiStopRequest
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport
from travel_agent.domain.place import Place


class DiscoveryRequest(AttractionModel):
    origin: Place
    departure_at: AwareDatetime | None = None
    available_minutes: int = Field(ge=30, le=720)
    stay_minutes: int = Field(ge=15, le=480)
    budget_yuan: float | None = Field(default=None, ge=0, le=100000)
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    require_confirmed_open: bool = False
    weather_policy: Literal["none", "inform", "exclude_signals"] = "none"
    excluded_attraction_ids: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_scope(self) -> "DiscoveryRequest":
        if self.departure_at is None and self.weather_policy != "none":
            raise ValueError("按到达时刻处理天气时，请提供出发时间或选择不查询天气")
        device = (self.origin.location_source == 'device'
                  and self.origin.location_accuracy_m is not None
                  and 0 < self.origin.location_accuracy_m <= 500
                  and self.origin.location_captured_at is not None)
        if self.origin.city_code != "440300" or not (self.origin.provider_place_id or device):
            raise ValueError("试点仅支持已点选的深圳高德出发地点")
        if self.stay_minutes >= self.available_minutes:
            raise ValueError("总可用时间需大于游览停留时间，并预留往返交通")
        return self


class DiscoveryCandidate(AttractionModel):
    card: AttractionCard
    request: MultiStopRequest
    totals: JourneyTotals
    estimated_transport_and_ticket_yuan: float | None
    reasons: list[str]
    warnings: list[str]
    route_sources: list[str] = Field(default_factory=list)
    route_fetched_at: AwareDatetime | None = None
    weather: MultiStopWeatherReport | None = None


class DiscoveryExclusion(AttractionModel):
    attraction_id: str
    name: str
    reason: str


class DiscoveryResult(AttractionModel):
    candidates: list[DiscoveryCandidate]
    excluded: list[DiscoveryExclusion]
    limitations: list[str]


class DiscoveryHistoryInfo(AttractionModel):
    thread_id: UUID
    revision: int = Field(ge=1)
    assistant_content: str | None = None


class DiscoveryRecommendInput(DiscoveryRequest):
    thread_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)


class DiscoveryRecommendResponse(DiscoveryResult):
    history: DiscoveryHistoryInfo | None = None


class DiscoveryChanges(AttractionModel):
    """Explicit partial user edits, retained while cross-field clarification is pending."""

    budget_yuan: float | None = Field(default=None, ge=0, le=100000)
    available_minutes: int | None = Field(default=None, ge=30, le=720)
    stay_minutes: int | None = Field(default=None, ge=15, le=480)
    priority: TravelPriority | None = None
    can_drive: bool | None = None
    max_walking_distance_m: float | None = Field(default=None, ge=0, le=100000)
    max_transfer_count: int | None = Field(default=None, ge=0, le=20)
    excluded_attraction_ids: list[str] | None = Field(default=None, max_length=3)


class DiscoveryRefineRequest(AttractionModel):
    request: DiscoveryRequest
    message: str = Field(min_length=1, max_length=4000)
    candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    selected_attraction_id: str | None = None
    pending_changes: DiscoveryChanges | None = None
    thread_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)


class DiscoveryRefineResponse(AttractionModel):
    status: Literal["updated", "clarification", "not_applicable", "cancelled"]
    request: DiscoveryRequest
    pending_changes: DiscoveryChanges | None = None
    result: DiscoveryResult | None = None
    answer: str
    history: DiscoveryHistoryInfo | None = None


class DiscoverySnapshot(AttractionModel):
    request: DiscoveryRequest
    result: DiscoveryResult
    pending_changes: DiscoveryChanges | None = None
    notice: str = ""
    stage: Literal["candidates", "journey"] = "candidates"
    selected_attraction_id: str | None = None
    draft_id: UUID | None = None
    plan_id: str | None = None
    revision: int = Field(default=0, ge=0)
    anchor_message_id: UUID | None = None


class DiscoveryChooseInput(AttractionModel):
    attraction_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    message: str | None = Field(default=None, min_length=1, max_length=4000)


class DiscoverySelectionInput(AttractionModel):
    draft_id: UUID
    plan_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class DiscoveryChooseResponse(AttractionModel):
    draft: MultiStopDraft
    history: DiscoveryHistoryInfo
