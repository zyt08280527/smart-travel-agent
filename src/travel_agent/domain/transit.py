from typing import Literal

from pydantic import BaseModel, Field

from travel_agent.domain.route import GeoPoint


class TransitLeg(BaseModel):
    """One normalized walking or vehicle leg in a transit option."""

    mode: Literal["walking", "bus", "subway", "railway", "taxi"]
    distance_m: float = Field(ge=0)
    duration_s: float | None = Field(default=None, ge=0)
    instruction: str | None = None
    line_name: str | None = None
    departure_stop: str | None = None
    arrival_stop: str | None = None
    via_stop_count: int | None = Field(default=None, ge=0)


class TransitOption(BaseModel):
    """One complete public-transport option from origin to destination."""

    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    walking_distance_m: float = Field(ge=0)
    cost_yuan: float | None = Field(default=None, ge=0)
    night_service: bool = False
    transfer_count: int = Field(ge=0)
    legs: list[TransitLeg] = Field(min_length=1)


class TransitPlan(BaseModel):
    """Normalized transit alternatives independent of the external API shape."""

    mode: Literal["transit"] = "transit"
    origin: GeoPoint
    destination: GeoPoint
    origin_city_code: str = Field(min_length=1)
    destination_city_code: str = Field(min_length=1)
    strategy: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8] = 0
    options: list[TransitOption] = Field(min_length=1)
    attribution: str = Field(min_length=1)
