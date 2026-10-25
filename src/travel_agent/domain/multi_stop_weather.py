from typing import Literal

from pydantic import AwareDatetime, Field

from travel_agent.domain.multi_stop import MultiStopModel


class StopWeather(MultiStopModel):
    stop_id: str
    name: str
    target_at: AwareDatetime
    status: Literal["forecast", "unavailable", "out_of_range", "past"]
    forecast_at: AwareDatetime | None = None
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    precipitation_mm: float | None = Field(default=None, ge=0)
    wind_speed_kmh: float | None = Field(default=None, ge=0)
    condition: str | None = None
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None


class MultiStopWeatherReport(MultiStopModel):
    plan_id: str
    queried_at: AwareDatetime
    provider: Literal["open-meteo"] = "open-meteo"
    source_url: str = "https://open-meteo.com/"
    stops: list[StopWeather]
    limitations: list[str]
