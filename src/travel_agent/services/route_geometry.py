"""Normalize provider route polylines for API cards and map rendering."""

from collections.abc import Iterable

from travel_agent.domain.route import GeoPoint
from travel_agent.services.coordinate_system import gcj02_to_wgs84

MAX_ROUTE_GEOMETRY_POINTS = 80


def parse_amap_polyline(value: object) -> list[GeoPoint]:
    """Parse one AMap GCJ-02 ``lon,lat;...`` polyline into WGS84 points."""
    if not isinstance(value, str) or not value.strip():
        return []

    points: list[GeoPoint] = []
    for pair in value.split(";"):
        parts = pair.split(",")
        if len(parts) != 2:
            continue
        try:
            longitude, latitude = (float(part) for part in parts)
            point = gcj02_to_wgs84(latitude, longitude)
        except ValueError:
            continue
        if not points or point != points[-1]:
            points.append(point)
    return points


def merge_geometry(parts: Iterable[Iterable[GeoPoint]]) -> list[GeoPoint]:
    """Join route fragments while dropping duplicate boundary points."""
    merged: list[GeoPoint] = []
    for part in parts:
        for point in part:
            if not merged or point != merged[-1]:
                merged.append(point)
    return merged


def compact_geometry(
    points: list[GeoPoint],
    *,
    max_points: int = MAX_ROUTE_GEOMETRY_POINTS,
) -> list[GeoPoint]:
    """Keep route endpoints and evenly sample long tracks for transport."""
    if max_points < 2:
        raise ValueError("max_points must be at least 2")
    if len(points) <= max_points:
        return points

    last_index = len(points) - 1
    indexes = [
        round(index * last_index / (max_points - 1))
        for index in range(max_points)
    ]
    return [points[index] for index in indexes]
