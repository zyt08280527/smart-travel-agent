import httpx
import pytest
import respx

from travel_agent.domain.route import GeoPoint
from travel_agent.services.amap_city import AMAP_REVERSE_GEOCODE_URL
from travel_agent.services.amap_coordinate import AMAP_COORDINATE_CONVERT_URL
from travel_agent.services.transit import (
    AMAP_TRANSIT_URL,
    TransitNotFoundError,
    TransitService,
    TransitServiceError,
)

ORIGIN = GeoPoint(latitude=22.5359023, longitude=113.9314749)
DESTINATION = GeoPoint(latitude=22.6009872, longitude=113.987959)


def mock_coordinate_and_city_requests() -> None:
    """Mock conversion once and reverse geocoding twice."""
    respx.get(AMAP_COORDINATE_CONVERT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "locations": "113.936340,22.532868;113.992928,22.598090",
            },
        )
    )
    respx.get(AMAP_REVERSE_GEOCODE_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "status": "1",
                    "regeocode": {
                        "addressComponent": {
                            "province": "广东省",
                            "city": "深圳市",
                            "citycode": "0755",
                            "district": "南山区",
                            "adcode": "440305",
                        }
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "status": "1",
                    "regeocode": {
                        "addressComponent": {
                            "province": "广东省",
                            "city": "深圳市",
                            "citycode": "0755",
                            "district": "南山区",
                            "adcode": "440305",
                        }
                    },
                },
            ),
        ]
    )


@pytest.mark.asyncio
@respx.mock
async def test_plan_transit_route_returns_normalized_plan() -> None:
    mock_coordinate_and_city_requests()
    transit_route = respx.get(AMAP_TRANSIT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "route": {
                    "transits": [
                        {
                            "cost": {
                                "duration": "2820",
                                "transit_fee": "",
                            },
                            "distance": "15103",
                            "walking_distance": "839",
                            "nightflag": "0",
                            "segments": [
                                {
                                    "walking": {
                                        "distance": "519",
                                        "cost": {"duration": "444"},
                                        "steps": [
                                            {
                                                "instruction": "步行至校巴站",
                                                "distance": "519",
                                                "polyline": (
                                                    "113.936340,22.532868;"
                                                    "113.937000,22.534000"
                                                ),
                                            }
                                        ],
                                    },
                                    "bus": {
                                        "buslines": [
                                            {
                                                "name": "深大校巴通勤车",
                                                "type": "普通公交线路",
                                                "distance": "14264",
                                                "cost": {"duration": "2100"},
                                                "departure_stop": {
                                                    "name": "粤海校区"
                                                },
                                                "arrival_stop": {
                                                    "name": "丽湖校区"
                                                },
                                                "via_num": "0",
                                                "polyline": (
                                                    "113.937000,22.534000;"
                                                    "113.991000,22.597000"
                                                ),
                                            }
                                        ]
                                    },
                                },
                                {
                                    "walking": {
                                        "distance": "320",
                                        "cost": {"duration": "276"},
                                        "steps": [
                                            {
                                                "instruction": "步行至终点",
                                                "distance": "320",
                                                "polyline": (
                                                    "113.991000,22.597000;"
                                                    "113.992928,22.598090"
                                                ),
                                            }
                                        ],
                                    },
                                    "bus": {"buslines": []},
                                },
                            ],
                        }
                    ]
                },
            },
        )
    )

    async with httpx.AsyncClient() as client:
        result = await TransitService(
            client,
            api_key="amap-test-key",
        ).plan_transit_route(ORIGIN, DESTINATION)

    option = result.options[0]
    assert result.mode == "transit"
    assert result.origin_city_code == "0755"
    assert option.duration_s == 2820
    assert option.cost_yuan is None
    assert option.walking_distance_m == 839
    assert option.transfer_count == 0
    assert [leg.mode for leg in option.legs] == [
        "walking",
        "bus",
        "walking",
    ]
    assert option.legs[1].line_name == "深大校巴通勤车"
    assert len(option.geometry) == 4
    assert all(leg.geometry for leg in option.legs)

    request = transit_route.calls.last.request
    assert request.url.params["origin"] == "113.936340,22.532868"
    assert request.url.params["destination"] == "113.992928,22.598090"
    assert request.url.params["city1"] == "0755"
    assert request.url.params["city2"] == "0755"
    assert request.url.params["show_fields"] == "cost,polyline"


@pytest.mark.asyncio
@respx.mock
async def test_plan_transit_route_rejects_missing_options() -> None:
    mock_coordinate_and_city_requests()
    respx.get(AMAP_TRANSIT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "1",
                "info": "OK",
                "route": {"transits": []},
            },
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(TransitNotFoundError, match="未找到.*公交路线"):
            await TransitService(
                client,
                api_key="amap-test-key",
            ).plan_transit_route(ORIGIN, DESTINATION)


@pytest.mark.asyncio
@respx.mock
async def test_plan_transit_route_hides_provider_failure() -> None:
    mock_coordinate_and_city_requests()
    respx.get(AMAP_TRANSIT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "0",
                "info": "INVALID_PARAMS",
            },
        )
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(
            TransitServiceError,
            match="公交路线服务暂时不可用",
        ):
            await TransitService(
                client,
                api_key="amap-test-key",
            ).plan_transit_route(ORIGIN, DESTINATION)
