from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Place(BaseModel):
    """A normalized place candidate returned by a search provider."""

    display_name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: str | None = None
    place_type: str | None = None
    importance: float | None = Field(default=None, ge=0)
    provider_place_id: str | None = None
    city_code: str | None = None
    location_source: Literal['device'] | None = None
    location_accuracy_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    location_captured_at: AwareDatetime | None = None


class DeviceLocationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(gt=0, le=500)
    captured_at: AwareDatetime


class PlaceSearchResult(BaseModel):
    """Normalized results for one free-form place query."""

    query: str = Field(min_length=1)
    places: list[Place] = Field(default_factory=list)
    attribution: str = Field(min_length=1)
    # Keep the old default so persisted v1 cards without a provider still load.
    provider: Literal["openstreetmap", "amap"] = "openstreetmap"


class RouteEndpointCandidates(BaseModel):
    """Candidate places for the origin and destination of one route request."""

    origin: PlaceSearchResult
    destination: PlaceSearchResult
    attribution: str = Field(min_length=1)
