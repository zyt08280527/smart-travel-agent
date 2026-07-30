import pytest
from pydantic import ValidationError

from travel_agent.domain.place import Place, PlaceSearchResult


def test_place_search_result_accepts_normalized_place() -> None:
    result = PlaceSearchResult(
        query="深圳大学",
        places=[
            Place(
                display_name="深圳大学，南山区，深圳市，广东省，中国",
                latitude=22.5333,
                longitude=113.9304,
                category="amenity",
                place_type="university",
                importance=0.6,
            )
        ],
        attribution="Data © OpenStreetMap contributors",
    )

    assert result.query == "深圳大学"
    assert result.places[0].place_type == "university"


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (91, 114),
        (-91, 114),
        (22, 181),
        (22, -181),
    ],
)
def test_place_rejects_coordinates_outside_earth(
    latitude: float,
    longitude: float,
) -> None:
    with pytest.raises(ValidationError):
        Place(
            display_name="无效地点",
            latitude=latitude,
            longitude=longitude,
        )
