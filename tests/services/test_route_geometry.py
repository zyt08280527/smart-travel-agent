from travel_agent.domain.route import GeoPoint
from travel_agent.services.route_geometry import (
    compact_geometry,
    merge_geometry,
    parse_amap_polyline,
)


def test_parse_amap_polyline_converts_gcj02_and_skips_invalid_pairs() -> None:
    points = parse_amap_polyline(
        "113.936580,22.533180;invalid;113.940000,22.540000"
    )

    assert len(points) == 2
    assert points[0].longitude < 113.936580
    assert points[0].latitude != 22.533180


def test_parse_amap_polyline_accepts_v5_nested_polyline_object() -> None:
    points = parse_amap_polyline(
        {
            "polyline": (
                "113.936580,22.533180;113.940000,22.540000"
            )
        }
    )

    assert len(points) == 2
    assert points[0].longitude < 113.936580


def test_merge_geometry_deduplicates_fragment_boundaries() -> None:
    first = GeoPoint(latitude=22.5, longitude=113.9)
    second = GeoPoint(latitude=22.6, longitude=114.0)
    third = GeoPoint(latitude=22.7, longitude=114.1)

    assert merge_geometry([[first, second], [second, third]]) == [
        first,
        second,
        third,
    ]


def test_compact_geometry_keeps_endpoints() -> None:
    points = [
        GeoPoint(latitude=22.0 + index / 100, longitude=113.0)
        for index in range(100)
    ]

    compacted = compact_geometry(points, max_points=10)

    assert len(compacted) == 10
    assert compacted[0] == points[0]
    assert compacted[-1] == points[-1]
