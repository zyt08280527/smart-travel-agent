from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from travel_agent.config import get_settings
from travel_agent.domain.route import GeoPoint
from travel_agent.observability.http import observed_request

AMAP_COORDINATE_CONVERT_URL = (
    "https://restapi.amap.com/v3/assistant/coordinate/convert"
)
AMAP_USER_AGENT = "smart-travel-agent/0.1 (learning project)"
AMAP_COORDINATE_BATCH_LIMIT = 40


class AmapCoordinate(BaseModel):
    """One GCJ-02 coordinate returned by AMap."""

    coordinate_system: Literal["gcj02"] = "gcj02"
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class AmapCoordinateServiceError(RuntimeError):
    """A user-safe error raised when coordinate conversion fails."""


class AmapCoordinateService:
    """Convert WGS84 coordinates to AMap GCJ-02 coordinates."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._external_client = client
        self._api_key = api_key

    async def convert_wgs84(
        self,
        points: list[GeoPoint],
    ) -> list[AmapCoordinate]:
        """Convert one to forty WGS84 points in one AMap request."""
        if not points:
            raise ValueError("至少需要一个待转换坐标")
        if len(points) > AMAP_COORDINATE_BATCH_LIMIT:
            raise ValueError(
                f"单次最多转换 {AMAP_COORDINATE_BATCH_LIMIT} 个坐标"
            )

        api_key = self._api_key or get_settings().amap_api_key.get_secret_value()
        if self._external_client is not None:
            return await self._fetch(self._external_client, api_key, points)

        proxy_url = get_settings().route_proxy_url
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._fetch(client, api_key, points)

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        points: list[GeoPoint],
    ) -> list[AmapCoordinate]:
        locations = "|".join(
            f"{point.longitude:.6f},{point.latitude:.6f}"
            for point in points
        )
        try:
            response = await observed_request(
                client,
                "GET",
                AMAP_COORDINATE_CONVERT_URL,
                provider="amap-coordinate",
                params={
                    "key": api_key,
                    "locations": locations,
                    "coordsys": "gps",
                    "output": "json",
                },
                headers={"User-Agent": AMAP_USER_AGENT},
            )
            response.raise_for_status()
            return self._parse_coordinates(
                response.json(),
                expected_count=len(points),
            )
        except AmapCoordinateServiceError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AmapCoordinateServiceError(
                "高德坐标转换服务暂时不可用，请稍后重试"
            ) from exc

    @staticmethod
    def _parse_coordinates(
        payload: Any,
        expected_count: int,
    ) -> list[AmapCoordinate]:
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise AmapCoordinateServiceError(
                "高德坐标转换服务暂时不可用，请稍后重试"
            )

        locations = payload.get("locations")
        if not isinstance(locations, str) or not locations:
            raise AmapCoordinateServiceError("高德坐标转换结果为空")

        coordinate_texts = locations.split(";")
        if len(coordinate_texts) != expected_count:
            raise AmapCoordinateServiceError("高德坐标转换结果数量不一致")

        converted: list[AmapCoordinate] = []
        for coordinate_text in coordinate_texts:
            longitude_text, latitude_text = coordinate_text.split(",")
            converted.append(
                AmapCoordinate(
                    latitude=latitude_text,
                    longitude=longitude_text,
                )
            )
        return converted
