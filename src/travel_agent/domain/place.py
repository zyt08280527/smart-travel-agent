from pydantic import BaseModel, Field


class Place(BaseModel):
    """A normalized place candidate returned by a search provider."""

    display_name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: str | None = None
    place_type: str | None = None
    importance: float | None = Field(default=None, ge=0)


class PlaceSearchResult(BaseModel):
    """Normalized results for one free-form place query."""

    query: str = Field(min_length=1)
    places: list[Place] = Field(default_factory=list)
    attribution: str = Field(min_length=1)


class RouteEndpointCandidates(BaseModel):
    """Candidate places for the origin and destination of one route request."""

    origin: PlaceSearchResult
    destination: PlaceSearchResult
    attribution: str = Field(min_length=1)
