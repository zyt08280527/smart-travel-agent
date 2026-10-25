"""Versioned business records, independent of Agent execution checkpoints."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from travel_agent.domain.decision import TravelComparisonResult
from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.domain.transit import TransitPlan


class LegacyTripSnapshot(BaseModel):
    """Preserve a v1 summary without inventing geometry or missing route data."""

    kind: Literal["legacy_summary"] = "legacy_summary"
    itinerary: SavedItinerary


class PlanningTripSnapshot(BaseModel):
    kind: Literal["planning"] = "planning"
    result: TravelComparisonResult

    @model_validator(mode="after")
    def selected_route_exists(self) -> "PlanningTripSnapshot":
        mode = self.result.selected_mode or self.result.recommendation.recommended_mode
        if mode not in {item.option.mode for item in self.result.recommendation.ranked_options}:
            raise ValueError("选中方式不在可执行候选中")
        if mode == "transit" and self.result.selected_transit_candidate_index is None:
            raise ValueError("公共交通必须明确选中候选")
        return self


class TransitTripSnapshot(BaseModel):
    kind: Literal["transit"] = "transit"
    result: TransitPlan
    selected_candidate_index: int = Field(ge=0)

    @model_validator(mode="after")
    def selected_route_exists(self) -> "TransitTripSnapshot":
        if self.selected_candidate_index >= len(self.result.options):
            raise ValueError("选中的公共交通候选不存在")
        return self


TripSnapshot = Annotated[
    LegacyTripSnapshot | PlanningTripSnapshot | TransitTripSnapshot,
    Field(discriminator="kind"),
]


class TripContent(BaseModel):
    """One complete single-leg snapshot; multi-stop planning is a later stage."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    timezone: str = "Asia/Shanghai"
    snapshot: TripSnapshot

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("未知业务时区") from exc
        return value


class TripOwner(BaseModel):
    """Ownership identity only; this record is not a login credential."""

    owner_id: UUID
    display_name: str = Field(min_length=1, max_length=100)
    created_at: AwareDatetime


class TripSummary(BaseModel):
    trip_id: UUID
    owner_id: UUID
    schema_version: Literal[2] = 2
    itinerary_type: Literal["single_leg"] = "single_leg"
    title: str
    timezone: str
    current_revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TripRevision(BaseModel):
    trip_id: UUID
    revision: int = Field(ge=1)
    reason: Literal["created", "edited", "adopted", "legacy_import"]
    content: TripContent
    created_at: AwareDatetime


class TripDetail(BaseModel):
    trip: TripSummary
    current: TripRevision


class LegacyImportReport(BaseModel):
    source_sha256: str
    valid_records: int
    invalid_lines: list[int]
    duplicate_ids: list[str]
    created: int = 0
    skipped: int = 0
    backup_path: str | None = None
    checked_at: datetime
