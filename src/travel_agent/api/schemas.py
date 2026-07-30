from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    """One user message sent to the Agent."""

    message: str = Field(min_length=1, max_length=4000)
    thread_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        """Trim surrounding whitespace and reject an empty message."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息不能为空")
        return normalized


class ApprovalRequest(BaseModel):
    """A human decision for one interrupted Agent action."""

    decision: Literal["approve", "reject"]
    message: str | None = Field(default=None, max_length=500)


class PendingAction(BaseModel):
    """One state-changing tool call waiting for human review."""

    name: str = Field(min_length=1)
    args: dict[str, Any]
    description: str = Field(min_length=1)
    allowed_decisions: list[Literal["approve", "reject"]]


class WeatherResultCard(BaseModel):
    """Normalized current-weather facts safe for structured display."""

    type: Literal["weather"]
    city: str
    country: str | None = None
    admin1: str | None = None
    temperature_c: float
    apparent_temperature_c: float
    precipitation_mm: float
    wind_speed_kmh: float
    condition: str
    observed_at: str


class RouteResultCard(BaseModel):
    """Normalized driving or walking route summary for structured display."""

    type: Literal["route"]
    mode: Literal["driving", "walking"]
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    step_count: int = Field(ge=0)
    attribution: str = Field(min_length=1)


class TransitResultCard(BaseModel):
    """Normalized first public-transport option for structured display."""

    type: Literal["transit"]
    option_count: int = Field(ge=1)
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    walking_distance_m: float = Field(ge=0)
    cost_yuan: float | None = Field(default=None, ge=0)
    transfer_count: int = Field(ge=0)
    line_names: list[str]
    attribution: str = Field(min_length=1)


class PlaceCandidateCard(BaseModel):
    """One normalized location candidate displayed in a search card."""

    display_name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: str | None = None
    place_type: str | None = None


class PlaceResultCard(BaseModel):
    """Normalized place-search candidates for structured display."""

    type: Literal["place"]
    query: str = Field(min_length=1)
    places: list[PlaceCandidateCard]
    attribution: str = Field(min_length=1)


class AgentResponse(BaseModel):
    """A completed answer or an interrupted action awaiting approval."""

    status: Literal["completed", "approval_required"]
    thread_id: UUID
    answer: str | None = None
    pending_actions: list[PendingAction] = Field(default_factory=list)


class AgentStreamEvent(BaseModel):
    """One stable application event emitted during an Agent run."""

    type: Literal[
        "run_started",
        "assistant_delta",
        "tool_requested",
        "tool_completed",
        "result_card",
        "approval_required",
        "final",
        "error",
        "done",
    ]
    thread_id: UUID
    delta: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_call_id: str | None = None
    card: (
        WeatherResultCard
        | RouteResultCard
        | TransitResultCard
        | PlaceResultCard
        | None
    ) = None
    pending_actions: list[PendingAction] = Field(default_factory=list)
    answer: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    """Application health and loaded Agent capabilities."""

    status: Literal["ok"]
    tools: list[str]
