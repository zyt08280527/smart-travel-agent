from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from travel_agent.domain.discovery import DiscoveryRequest
from travel_agent.domain.place import DeviceLocationInput
from travel_agent.services.amap_city import AmapCityInfo
from travel_agent.services.amap_coordinate import AmapCoordinate
from travel_agent.services.device_location import DeviceLocationService

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def position(**updates):
    return DeviceLocationInput.model_validate({
        'latitude': 22.53, 'longitude': 113.93, 'accuracy_m': 30,
        'captured_at': NOW, **updates,
    })


@pytest.mark.asyncio
async def test_converts_for_address_but_keeps_actual_wgs84_for_route():
    converter, cities = AsyncMock(), AsyncMock()
    converted = AmapCoordinate(latitude=22.527, longitude=113.935)
    converter.convert_wgs84.return_value = [converted]
    cities.resolve_city.return_value = AmapCityInfo(
        coordinate=converted, city_name='深圳市', city_code='0755', adcode='440305',
        province='广东省', district='南山区', formatted_address='广东省深圳市南山区某路',
    )
    service = DeviceLocationService(converter, cities)
    place = await service.resolve(position(), now=NOW)
    assert place.latitude == 22.53 and place.longitude == 113.93
    assert place.location_source == 'device' and place.provider_place_id is None
    assert place.city_code == '440300' and '某路' in place.display_name
    cities.resolve_city.assert_awaited_once_with(converted)
    request = DiscoveryRequest(origin=place, available_minutes=240, stay_minutes=60)
    assert request.origin == place
    cities.resolve_city.return_value.adcode = '440106'
    with pytest.raises(ValueError, match='不在深圳'):
        await service.resolve(position(), now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [-301, 61])
async def test_stale_or_future_fix_does_not_call_provider(offset):
    converter = AsyncMock()
    service = DeviceLocationService(converter, AsyncMock())
    with pytest.raises(ValueError, match='时间'):
        await service.resolve(position(captured_at=NOW + timedelta(seconds=offset)), now=NOW)
    converter.convert_wgs84.assert_not_awaited()


@pytest.mark.parametrize('updates', [
    {'accuracy_m': 501}, {'accuracy_m': 0}, {'latitude': float('nan')},
    {'longitude': 200}, {'captured_at': '2030-01-01T00:00:00'},
])
def test_invalid_positions_rejected(updates):
    with pytest.raises(ValidationError):
        position(**updates)
