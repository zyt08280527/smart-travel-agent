import pytest

from travel_agent.services.coordinate_system import gcj02_to_wgs84


def test_gcj02_to_wgs84_removes_mainland_china_offset() -> None:
    converted = gcj02_to_wgs84(22.54, 113.94)

    assert converted.latitude == pytest.approx(22.5427, abs=0.001)
    assert converted.longitude == pytest.approx(113.9349, abs=0.001)


def test_gcj02_to_wgs84_leaves_overseas_coordinate_unchanged() -> None:
    converted = gcj02_to_wgs84(40.7128, -74.006)

    assert converted.latitude == 40.7128
    assert converted.longitude == -74.006
