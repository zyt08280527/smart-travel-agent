import httpx
import pytest
import respx

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_coordinate import AMAP_COORDINATE_CONVERT_URL
from travel_agent.services.amap_walking import (
    AMAP_WALKING_URL,
    AmapWalkingRouteService,
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
async def test_amap_walking_converts_coordinates_and_normalizes_route() -> None:
    mock_coordinate_conversion()
    route = respx.get(AMAP_WALKING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "route": {
                    "paths": [
                        {
                            "distance": "5200",
                            "cost": {"duration": "4200"},
                            "steps": [
                                {
                                    "instruction": "沿学府路向东步行",
                                    "road_name": "学府路",
                                    "step_distance": "800",
                                    "cost": {"duration": "600"},
                                    "navi": {"action": "直行"},
                                    "polyline": (
                                        "113.936580,22.533180;"
                                        "113.940000,22.540000"
                                    ),
                                }
                            ],
                        }
                    ]
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await AmapWalkingRouteService(
            client,
            api_key="amap-test-key",
        ).plan_walking_route(ORIGIN, DESTINATION)

    assert result.mode == "walking"
    assert result.distance_m == 5200
    assert result.duration_s == 4200
    assert result.duration_basis == "static_without_live_traffic"
    assert result.steps[0].instruction == "沿学府路向东步行"
    assert len(result.geometry) == 2
    assert result.attribution.startswith("步行路线数据来源：高德地图")

    request = route.calls.last.request
    assert request.url.params["origin"] == "113.936580,22.533180"
    assert request.url.params["destination"] == "113.993070,22.598250"
    assert request.url.params["show_fields"] == "cost,navi,polyline"


@pytest.mark.asyncio
@respx.mock
async def test_amap_walking_rejects_missing_route() -> None:
    mock_coordinate_conversion()
    respx.get(AMAP_WALKING_URL).mock(
        return_value=httpx.Response(
            200,
            json={"status": "1", "route": {"paths": []}},
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteNotFoundError, match="未找到"):
            await AmapWalkingRouteService(
                client,
                api_key="amap-test-key",
            ).plan_walking_route(ORIGIN, DESTINATION)


@pytest.mark.asyncio
@respx.mock
async def test_amap_walking_hides_provider_failure() -> None:
    mock_coordinate_conversion()
    respx.get(AMAP_WALKING_URL).mock(return_value=httpx.Response(503))

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteServiceError, match="暂时不可用"):
            await AmapWalkingRouteService(
                client,
                api_key="amap-test-key",
            ).plan_walking_route(ORIGIN, DESTINATION)
