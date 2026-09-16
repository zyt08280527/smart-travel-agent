import math

from travel_agent.domain.route import GeoPoint

_PI = math.pi
_AXIS = 6378245.0
_ECCENTRICITY_SQUARED = 0.006693421622965943


def gcj02_to_wgs84(latitude: float, longitude: float) -> GeoPoint:
    """Approximate one mainland-China GCJ-02 point as WGS84."""
    if _outside_china(latitude, longitude):
        return GeoPoint(latitude=latitude, longitude=longitude)

    transformed_latitude = _transform_latitude(
        longitude - 105.0,
        latitude - 35.0,
    )
    transformed_longitude = _transform_longitude(
        longitude - 105.0,
        latitude - 35.0,
    )
    latitude_radians = latitude / 180.0 * _PI
    magic = math.sin(latitude_radians)
    magic = 1 - _ECCENTRICITY_SQUARED * magic * magic
    sqrt_magic = math.sqrt(magic)
    latitude_delta = (
        transformed_latitude * 180.0
        / (
            (_AXIS * (1 - _ECCENTRICITY_SQUARED))
            / (magic * sqrt_magic)
            * _PI
        )
    )
    longitude_delta = (
        transformed_longitude * 180.0
        / (_AXIS / sqrt_magic * math.cos(latitude_radians) * _PI)
    )
    return GeoPoint(
        latitude=latitude * 2 - (latitude + latitude_delta),
        longitude=longitude * 2 - (longitude + longitude_delta),
    )


def _outside_china(latitude: float, longitude: float) -> bool:
    return not (72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271)


def _transform_latitude(x: float, y: float) -> float:
    result = (
        -100.0
        + 2.0 * x
        + 3.0 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
    )
    result += (
        20.0 * math.sin(6.0 * x * _PI)
        + 20.0 * math.sin(2.0 * x * _PI)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(y * _PI)
        + 40.0 * math.sin(y / 3.0 * _PI)
    ) * 2.0 / 3.0
    result += (
        160.0 * math.sin(y / 12.0 * _PI)
        + 320 * math.sin(y * _PI / 30.0)
    ) * 2.0 / 3.0
    return result


def _transform_longitude(x: float, y: float) -> float:
    result = (
        300.0
        + x
        + 2.0 * y
        + 0.1 * x * x
        + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
    )
    result += (
        20.0 * math.sin(6.0 * x * _PI)
        + 20.0 * math.sin(2.0 * x * _PI)
    ) * 2.0 / 3.0
    result += (
        20.0 * math.sin(x * _PI)
        + 40.0 * math.sin(x / 3.0 * _PI)
    ) * 2.0 / 3.0
    result += (
        150.0 * math.sin(x / 12.0 * _PI)
        + 300.0 * math.sin(x / 30.0 * _PI)
    ) * 2.0 / 3.0
    return result
