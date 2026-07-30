import json

import httpx
import pytest
import respx

from travel_agent.domain.route import GeoPoint
from travel_agent.services.route import RouteNotFoundError, RouteServiceError
from travel_agent.services.walking_route import (
    WALKING_ROUTE_URL,
    WalkingRouteService,
)

ORIGIN = GeoPoint(latitude=22.5359, longitude=113.9315)
DESTINATION = GeoPoint(latitude=22.5400, longitude=113.9400)


@pytest.mark.asyncio
@respx.mock
async def test_plan_walking_route_returns_normalized_domain_model() -> None:
    route = respx.post(WALKING_ROUTE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {
                            "summary": {
                                "distance": 1_200.4,
                                "duration": 900.2,
                            },
                            "segments": [
                                {
                                    "steps": [
                                        {
                                            "distance": 300.0,
                                            "duration": 220.0,
                                            "type": 11,
                                            "instruction": "向北步行",
                                            "name": "-",
                                        }
                                    ]
                                }
                            ],
                        },
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [113.9315, 22.5359],
                                [113.9400, 22.5400],
                            ],
                        },
                    }
                ],
                "metadata": {
                    "attribution": (
                        "openrouteservice.org by HeiGIT | "
                        "Map data © OpenStreetMap contributors"
                    )
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await WalkingRouteService(
            client,
            api_key="ors-test-key",
        ).plan_walking_route(ORIGIN, DESTINATION)

    assert result.mode == "walking"
    assert result.distance_m == 1_200.4
    assert result.steps[0].instruction == "向北步行"
    assert result.steps[0].road_name is None
    assert result.geometry[-1] == DESTINATION
    assert result.attribution.startswith("openrouteservice.org")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "ors-test-key"
    payload = json.loads(request.content)
    assert payload["coordinates"] == [
        [ORIGIN.longitude, ORIGIN.latitude],
        [DESTINATION.longitude, DESTINATION.latitude],
    ]
    assert payload["language"] == "zh-cn"


@pytest.mark.asyncio
@respx.mock
async def test_plan_walking_route_rejects_missing_route() -> None:
    respx.post(WALKING_ROUTE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": []},
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteNotFoundError, match="未找到.*步行路线"):
            await WalkingRouteService(
                client,
                api_key="ors-test-key",
            ).plan_walking_route(ORIGIN, DESTINATION)


@pytest.mark.asyncio
@respx.mock
async def test_plan_walking_route_hides_upstream_failure() -> None:
    respx.post(WALKING_ROUTE_URL).mock(return_value=httpx.Response(401))

    async with httpx.AsyncClient() as client:
        with pytest.raises(RouteServiceError, match="步行路线服务暂时不可用"):
            await WalkingRouteService(
                client,
                api_key="ors-test-key",
            ).plan_walking_route(ORIGIN, DESTINATION)
