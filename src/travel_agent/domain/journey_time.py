"""Provider-independent departure-time models for journey planning."""

from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

DeparturePrecision = Literal["now", "exact", "time_period", "date_only"]
DepartureRelation = Literal["past", "immediate", "future"]
WeatherQueryKind = Literal["past", "current", "forecast", "out_of_range"]


class DepartureTime(BaseModel):
    """One resolved departure instant plus how precisely the user expressed it."""

    departure_at: datetime
    timezone: str = Field(default="Asia/Shanghai", min_length=1)
    precision: DeparturePrecision
    source_text: str = Field(min_length=1)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Require an IANA timezone that can be shared across system layers."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def normalize_departure_timezone(self) -> "DepartureTime":
        """Reject ambiguous naive times and normalize aware values to the business zone."""
        if self.departure_at.tzinfo is None or self.departure_at.utcoffset() is None:
            raise ValueError("departure_at must include timezone information")
        self.departure_at = self.departure_at.astimezone(ZoneInfo(self.timezone))
        return self

    def relation_to(
        self,
        reference_at: datetime,
        *,
        immediate_window_minutes: int = 15,
    ) -> DepartureRelation:
        """Classify the departure without reading the system clock implicitly."""
        if reference_at.tzinfo is None or reference_at.utcoffset() is None:
            raise ValueError("reference_at must include timezone information")
        if immediate_window_minutes < 0:
            raise ValueError("immediate_window_minutes cannot be negative")

        delta = self.departure_at - reference_at.astimezone(
            ZoneInfo(self.timezone)
        )
        window = timedelta(minutes=immediate_window_minutes)
        if delta < -window:
            return "past"
        if delta <= window:
            return "immediate"
        return "future"

    def weather_query_kind(
        self,
        reference_at: datetime,
        *,
        forecast_days: int = 16,
        immediate_window_minutes: int = 15,
    ) -> WeatherQueryKind:
        """Choose current weather, forecast, or a deterministic rejection path."""
        if forecast_days < 1:
            raise ValueError("forecast_days must be at least 1")

        relation = self.relation_to(
            reference_at,
            immediate_window_minutes=immediate_window_minutes,
        )
        if relation == "past":
            return "past"
        if relation == "immediate":
            return "current"
        local_reference = reference_at.astimezone(ZoneInfo(self.timezone))
        last_forecast_date = local_reference.date() + timedelta(
            days=forecast_days - 1
        )
        if self.departure_at.date() > last_forecast_date:
            return "out_of_range"
        return "forecast"


class ArrivalDeadline(BaseModel):
    """A required arrival instant and an explicit planning buffer."""

    arrival_by: datetime
    timezone: str = Field(default="Asia/Shanghai", min_length=1)
    precision: DeparturePrecision
    source_text: str = Field(min_length=1)
    buffer_minutes: int = Field(default=15, ge=0, le=180)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def normalize_arrival_timezone(self) -> "ArrivalDeadline":
        if self.arrival_by.tzinfo is None or self.arrival_by.utcoffset() is None:
            raise ValueError("arrival_by must include timezone information")
        self.arrival_by = self.arrival_by.astimezone(ZoneInfo(self.timezone))
        return self

    def latest_departure_at(self, duration_s: float) -> datetime:
        """Return the latest departure that preserves route time and buffer."""
        if duration_s < 0:
            raise ValueError("duration_s cannot be negative")
        return self.arrival_by - timedelta(
            seconds=duration_s,
            minutes=self.buffer_minutes,
        )
