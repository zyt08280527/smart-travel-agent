from pydantic import BaseModel, Field


class Location(BaseModel):
    """A resolved city location."""

    name: str
    country: str | None = None
    admin1: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class CurrentWeather(BaseModel):
    """Provider-independent current weather data."""

    location: Location
    temperature_c: float
    apparent_temperature_c: float
    precipitation_mm: float = Field(ge=0)
    wind_speed_kmh: float = Field(ge=0)
    weather_code: int
    condition: str
    observed_at: str


class ForecastWeather(BaseModel):
    """Provider-independent hourly weather forecast for a departure time."""

    location: Location
    temperature_c: float
    apparent_temperature_c: float
    precipitation_mm: float = Field(ge=0)
    wind_speed_kmh: float = Field(ge=0)
    weather_code: int
    condition: str
    forecast_at: str


WeatherData = CurrentWeather | ForecastWeather
