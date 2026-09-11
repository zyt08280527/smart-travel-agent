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


class TrafficSegment(BaseModel):
    """One traffic-condition segment returned by a traffic-aware provider."""

    status: Literal["未知", "畅通", "缓行", "拥堵", "严重拥堵"]
    distance_m: float = Field(ge=0)
    road_name: str | None = None


class RoutePlan(BaseModel):
    """A normalized route independent of travel mode and routing provider."""

    mode: Literal["driving", "walking"] = "driving"
    origin: GeoPoint
    destination: GeoPoint
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    duration_basis: Literal[
        "static_without_live_traffic",
        "traffic_aware_estimate",
    ] = "static_without_live_traffic"
    tolls_yuan: float | None = Field(default=None, ge=0)
    taxi_cost_yuan: float | None = Field(default=None, ge=0)
    traffic_lights: int | None = Field(default=None, ge=0)
    restriction: Literal[0, 1] | None = None
    traffic_segments: list[TrafficSegment] = Field(default_factory=list)
    steps: list[RouteStep] = Field(default_factory=list)
    geometry: list[GeoPoint] = Field(default_factory=list)
    attribution: str = Field(min_length=1)
