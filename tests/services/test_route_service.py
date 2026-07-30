import httpx
import pytest
import respx

from travel_agent.domain.route import GeoPoint
from travel_agent.services.route import (
    ROUTE_BASE_URL,
    RouteNotFoundError,
    RouteService,
    RouteServiceError,
)

ORIGIN = GeoPoint(latitude=22.5359, longitude=113.9315)
DESTINATION = GeoPoint(latitude=22.5726, longitude=114.2146)
ROUTE_URL = f"{ROUTE_BASE_URL}/113.9315,22.5359;114.2146,22.5726"


@pytest.mark.asyncio
@respx.mock
async def test_plan_driving_route_returns_normalized_domain_model() -> None:
    respx.get(ROUTE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [
                    {
                        "distance": 35_200,
                        "duration": 3_600,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [113.9315, 22.5359],
                                [114.2146, 22.5726],
                            ],
                        },
                        "legs": [
                            {
                                "steps": [
                                    {
                                        "distance": 500,
                                        "duration": 60,
                                        "name": "南海大道",
                                        "maneuver": {
                                            "type": "depart",
                                            "modifier": "straight",
                                        },
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await RouteService(client).plan_driving_route(ORIGIN, DESTINATION)

    assert result.distance_m == 35_200
    assert result.duration_s == 3_600
    assert result.steps[0].road_name == "南海大道"
    assert result.geometry[-1] == DESTINATION


@pytest.mark.asyncio
@respx.mock
async def test_plan_driving_route_rejects_missing_route() -> None:
    respx.get(ROUTE_URL).mock(
        return_value=httpx.Response(200, json={"code": "NoRoute", "routes": []})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteNotFoundError, match="未找到"):
            await RouteService(client).plan_driving_route(ORIGIN, DESTINATION)


@pytest.mark.asyncio
@respx.mock
async def test_plan_driving_route_hides_upstream_failure() -> None:
    respx.get(ROUTE_URL).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteServiceError, match="路线规划服务暂时不可用"):
            await RouteService(client).plan_driving_route(ORIGIN, DESTINATION)
