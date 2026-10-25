"""Resolve an explicitly supplied device position without snapping it to a POI."""

from datetime import UTC, datetime, timedelta

from travel_agent.domain.place import DeviceLocationInput, Place
from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_city import AmapCityService
from travel_agent.services.amap_coordinate import AmapCoordinateService


class DeviceLocationService:
    def __init__(self, coordinate_service=None, city_service=None):
        self.coordinates = coordinate_service or AmapCoordinateService()
        self.cities = city_service or AmapCityService()

    async def resolve(self, position: DeviceLocationInput, *, now=None) -> Place:
        now = now or datetime.now(UTC)
        age = now - position.captured_at
        if age > timedelta(minutes=5) or age < -timedelta(seconds=60):
            raise ValueError("定位时间已过期或设备时钟异常，请重新定位或手动选择出发点")
        point = GeoPoint(latitude=position.latitude, longitude=position.longitude)
        converted = await self.coordinates.convert_wgs84([point])
        city = await self.cities.resolve_city(converted[0])
        if len(city.adcode) != 6 or not city.adcode.startswith("4403"):
            raise ValueError("当前位置不在深圳，本阶段仅支持深圳目的地推荐，请手动选择深圳出发点")
        address = city.formatted_address or f"{city.city_name}{city.district or ''}附近"
        return Place(
            display_name=f"当前位置：{address}",
            latitude=point.latitude,
            longitude=point.longitude,
            city_code="440300",
            location_source="device",
            location_accuracy_m=position.accuracy_m,
            location_captured_at=position.captured_at,
        )
