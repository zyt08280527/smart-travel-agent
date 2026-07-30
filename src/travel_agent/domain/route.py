from typing import Literal

from pydantic import BaseModel, Field


class GeoPoint(BaseModel):
    """A WGS84 geographic coordinate used by the routing domain."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RouteStep(BaseModel):
    """One normalized maneuver in a route."""

    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    road_name: str | None = None
    maneuver_type: str = Field(min_length=1)
    maneuver_modifier: str | None = None
    instruction: str | None = None


class RoutePlan(BaseModel):
    """A normalized route independent of travel mode and routing provider."""

    mode: Literal["driving", "walking"] = "driving"
    origin: GeoPoint
    destination: GeoPoint
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    steps: list[RouteStep] = Field(default_factory=list)
    geometry: list[GeoPoint] = Field(default_factory=list)
    attribution: str = Field(min_length=1)
