from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ItineraryDraft(BaseModel):
    """An itinerary proposed by the Agent before it is saved."""

    title: str = Field(min_length=1, max_length=100)
    origin: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    travel_mode: Literal["driving", "walking", "transit"]
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    duration_basis: Literal["static_without_live_traffic"] = (
        "static_without_live_traffic"
    )
    notes: str | None = Field(default=None, max_length=1000)


class SavedItinerary(ItineraryDraft):
    """An itinerary record that has been persisted."""

    itinerary_id: str = Field(min_length=1)
    saved_at: datetime
