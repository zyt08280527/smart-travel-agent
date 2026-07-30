from typing import Any

import httpx
from pydantic import BaseModel, Field

from travel_agent.config import get_settings
from travel_agent.observability.http import observed_request
from travel_agent.services.amap_coordinate import (
    AMAP_USER_AGENT,
    AmapCoordinate,
)

AMAP_REVERSE_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/regeo"


class AmapCityInfo(BaseModel):
    """City metadata resolved from one AMap GCJ-02 coordinate."""

    coordinate: AmapCoordinate
    city_name: str = Field(min_length=1)
    city_code: str = Field(min_length=1)
    adcode: str = Field(min_length=1)
    province: str = Field(min_length=1)
    district: str | None = None


class AmapCityServiceError(RuntimeError):
    """A user-safe error raised when city metadata cannot be resolved."""


class AmapCityService:
    """Resolve AMap city metadata for one GCJ-02 coordinate."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
    ) -> None:
        self._external_client = client
        self._api_key = api_key

    async def resolve_city(self, coordinate: AmapCoordinate) -> AmapCityInfo:
        """Resolve citycode and adcode for one converted coordinate."""
        api_key = self._api_key or get_settings().amap_api_key.get_secret_value()
        if self._external_client is not None:
            return await self._fetch(
                self._external_client,
                api_key,
                coordinate,
            )

        proxy_url = get_settings().route_proxy_url
        timeout = httpx.Timeout(10.0, connect=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            proxy=str(proxy_url) if proxy_url is not None else None,
            trust_env=False,
        ) as client:
            return await self._fetch(client, api_key, coordinate)

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        coordinate: AmapCoordinate,
    ) -> AmapCityInfo:
        try:
            response = await observed_request(
                client,
                "GET",
                AMAP_REVERSE_GEOCODE_URL,
                provider="amap-regeocode",
                params={
                    "key": api_key,
                    "location": (
                        f"{coordinate.longitude:.6f},"
                        f"{coordinate.latitude:.6f}"
                    ),
                    "extensions": "base",
                    "output": "json",
                },
                headers={"User-Agent": AMAP_USER_AGENT},
            )
            response.raise_for_status()
            return self._parse_city(response.json(), coordinate)
        except AmapCityServiceError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AmapCityServiceError(
                "高德城市解析服务暂时不可用，请稍后重试"
            ) from exc

    @staticmethod
    def _parse_city(
        payload: Any,
        coordinate: AmapCoordinate,
    ) -> AmapCityInfo:
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise AmapCityServiceError(
                "高德城市解析服务暂时不可用，请稍后重试"
            )

        component = payload["regeocode"]["addressComponent"]
        province = component["province"]
        city = component.get("city")
        city_name = city if isinstance(city, str) and city else province
        city_code = component["citycode"]
        adcode = component["adcode"]
        district = component.get("district")
        return AmapCityInfo(
            coordinate=coordinate,
            city_name=city_name,
            city_code=city_code,
            adcode=adcode,
            province=province,
            district=district if isinstance(district, str) and district else None,
        )
