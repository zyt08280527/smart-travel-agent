import httpx
import pytest
import respx

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_coordinate import AMAP_COORDINATE_CONVERT_URL
from travel_agent.services.amap_driving import (
    AMAP_DRIVING_URL,
    AmapDrivingRouteService,
)
from travel_agent.services.route import RouteNotFoundError, RouteServiceError

ORIGIN = GeoPoint(latitude=22.5359023, longitude=113.9314749)
DESTINATION = GeoPoint(latitude=22.6009872, longitude=113.987959)


def mock_coordinate_conversion() -> None:
    respx.get(AMAP_COORDINATE_CONVERT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "locations": "113.936580,22.533180;113.993070,22.598250",
            },
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_amap_driving_returns_traffic_aware_costs_and_segments() -> None:
    mock_coordinate_conversion()
    route = respx.get(AMAP_DRIVING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "route": {
                    "taxi_cost": "38.5",
                    "paths": [
                        {
                            "distance": "12800",
                            "restriction": "0",
                            "cost": {
                                "duration": "2100",
                                "tolls": "6",
                                "traffic_lights": "12",
                            },
                            "steps": [
                                {
                                    "instruction": "沿南海大道向北行驶",
                                    "road_name": "南海大道",
                                    "step_distance": "1500",
                                    "cost": {"duration": "240"},
                                    "navi": {"action": "直行"},
                                    "polyline": (
                                        "113.936580,22.533180;"
                                        "113.940000,22.540000"
                                    ),
                                    "tmcs": [
                                        {
                                            "tmc_status": "拥堵",
                                            "tmc_distance": "300",
                                        },
                                        {
                                            "tmc_status": "畅通",
                                            "tmc_distance": "1200",
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await AmapDrivingRouteService(
            client,
            api_key="amap-test-key",
        ).plan_driving_route(ORIGIN, DESTINATION)

    assert result.duration_basis == "traffic_aware_estimate"
    assert result.duration_s == 2100
    assert result.tolls_yuan == 6
    assert result.taxi_cost_yuan == 38.5
    assert result.traffic_lights == 12
    assert result.restriction == 0
    assert result.traffic_segments[0].status == "拥堵"
    assert result.traffic_segments[0].road_name == "南海大道"
    assert len(result.geometry) == 2
    assert result.geometry[0].longitude < 113.936580

    request = route.calls.last.request
    assert request.url.params["origin"] == "113.936580,22.533180"
    assert request.url.params["destination"] == "113.993070,22.598250"
    assert request.url.params["strategy"] == "32"
    assert request.url.params["show_fields"] == "cost,tmcs,navi,polyline"


@pytest.mark.asyncio
@respx.mock
async def test_amap_driving_rejects_missing_route() -> None:
    mock_coordinate_conversion()
    respx.get(AMAP_DRIVING_URL).mock(
        return_value=httpx.Response(
            200,
            json={"status": "1", "route": {"paths": []}},
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteNotFoundError, match="未找到"):
            await AmapDrivingRouteService(
                client,
                api_key="amap-test-key",
            ).plan_driving_route(ORIGIN, DESTINATION)


@pytest.mark.asyncio
@respx.mock
async def test_amap_driving_hides_provider_failure() -> None:
    mock_coordinate_conversion()
    respx.get(AMAP_DRIVING_URL).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteServiceError, match="暂时不可用"):
            await AmapDrivingRouteService(
                client,
                api_key="amap-test-key",
            ).plan_driving_route(ORIGIN, DESTINATION)
