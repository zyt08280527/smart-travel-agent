import pytest
from pydantic import ValidationError

from travel_agent.domain.route import GeoPoint
from travel_agent.domain.transit import TransitLeg, TransitOption, TransitPlan


def build_transit_plan() -> TransitPlan:
    """Build one valid normalized transit plan for domain tests."""
    return TransitPlan(
        origin=GeoPoint(latitude=22.5359023, longitude=113.9314749),
        destination=GeoPoint(latitude=22.6009872, longitude=113.987959),
        origin_city_code="0755",
        destination_city_code="0755",
        options=[
            TransitOption(
                distance_m="15200",
                duration_s="3600",
                walking_distance_m="900",
                cost_yuan="6",
                transfer_count=1,
                legs=[
                    TransitLeg(
                        mode="walking",
                        distance_m="500",
                        duration_s="420",
                        instruction="步行至深大站",
                    ),
                    TransitLeg(
                        mode="subway",
                        distance_m="12000",
                        duration_s="2100",
                        line_name="地铁1号线",
                        departure_stop="深大站",
                        arrival_stop="西丽站",
                        via_stop_count=6,
                    ),
                ],
            )
        ],
        attribution="© 高德软件有限公司",
    )


def test_transit_plan_converts_numeric_strings_and_keeps_transfer_details() -> None:
    plan = build_transit_plan()

    assert plan.mode == "transit"
    assert plan.options[0].duration_s == 3600
    assert plan.options[0].cost_yuan == 6
    assert plan.options[0].legs[1].line_name == "地铁1号线"
    assert plan.options[0].legs[1].via_stop_count == 6


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("distance_m", -1),
        ("duration_s", -1),
        ("walking_distance_m", -1),
        ("transfer_count", -1),
    ],
)
def test_transit_option_rejects_negative_measurements(
    field_name: str,
    invalid_value: float,
) -> None:
    values = {
        "distance_m": 1000,
        "duration_s": 600,
        "walking_distance_m": 200,
        "transfer_count": 0,
        "legs": [TransitLeg(mode="walking", distance_m=200)],
    }
    values[field_name] = invalid_value

    with pytest.raises(ValidationError):
        TransitOption.model_validate(values)


def test_transit_leg_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        TransitLeg.model_validate(
            {
                "mode": "spaceship",
                "distance_m": 100,
            }
        )


def test_transit_plan_requires_at_least_one_option() -> None:
    plan = build_transit_plan()

    with pytest.raises(ValidationError):
        TransitPlan.model_validate(
            {
                **plan.model_dump(),
                "options": [],
            }
        )
