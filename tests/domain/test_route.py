import pytest
from pydantic import ValidationError

from travel_agent.domain.route import GeoPoint, RoutePlan, RouteStep


def test_route_plan_accepts_normalized_driving_route() -> None:
    route = RoutePlan(
        origin=GeoPoint(latitude=22.5359, longitude=113.9315),
        destination=GeoPoint(latitude=22.5726, longitude=114.2146),
        distance_m=35_200,
        duration_s=3_600,
        steps=[
            RouteStep(
                distance_m=500,
                duration_s=60,
                road_name="南海大道",
                maneuver_type="depart",
                maneuver_modifier="straight",
            )
        ],
        geometry=[
            GeoPoint(latitude=22.5359, longitude=113.9315),
            GeoPoint(latitude=22.5726, longitude=114.2146),
        ],
        attribution="Routing data © OpenStreetMap contributors",
    )

    assert route.mode == "driving"
    assert route.distance_m == 35_200
    assert route.steps[0].maneuver_type == "depart"


def test_route_plan_accepts_walking_mode() -> None:
    route = RoutePlan(
        mode="walking",
        origin=GeoPoint(latitude=22.5359, longitude=113.9315),
        destination=GeoPoint(latitude=22.5400, longitude=113.9400),
        distance_m=1_200,
        duration_s=900,
        attribution="openrouteservice.org by HeiGIT | Map data © OpenStreetMap",
    )

    assert route.mode == "walking"


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("distance_m", -1),
        ("duration_s", -1),
    ],
)
def test_route_plan_rejects_negative_measurements(
    field_name: str,
    invalid_value: float,
) -> None:
    values = {
        "origin": GeoPoint(latitude=22.5, longitude=113.9),
        "destination": GeoPoint(latitude=22.6, longitude=114.2),
        "distance_m": 1_000,
        "duration_s": 600,
        "attribution": "Routing data © OpenStreetMap contributors",
    }
    values[field_name] = invalid_value

    with pytest.raises(ValidationError):
        RoutePlan.model_validate(values)


def test_geo_point_rejects_invalid_latitude() -> None:
    with pytest.raises(ValidationError):
        GeoPoint(latitude=91, longitude=114)
