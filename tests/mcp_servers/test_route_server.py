import json

import pytest
from mcp.types import CallToolResult, TextContent

from travel_agent.domain.route import GeoPoint, RoutePlan, RouteStep
from travel_agent.mcp_servers.route_server import (
    _build_agent_payload,
    plan_driving_route,
    plan_transit_route,
    plan_walking_route,
)


def _parse_text_result(result: CallToolResult) -> dict[str, object]:
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert result.meta == {
        "observability": {
            "external_http_request_count": 0,
            "external_http_requests": [],
        }
    }
    return json.loads(content.text)


def test_build_agent_payload_includes_compact_geometry_coordinates() -> None:
    route = RoutePlan(
        origin=GeoPoint(latitude=22.5, longitude=113.9),
        destination=GeoPoint(latitude=22.6, longitude=114.0),
        distance_m=1200,
        duration_s=300,
        steps=[
            RouteStep(
                distance_m=1200,
                duration_s=300,
                road_name="测试路",
                maneuver_type="depart",
            )
        ],
        geometry=[
            GeoPoint(latitude=22.5, longitude=113.9),
            GeoPoint(latitude=22.6, longitude=114.0),
        ],
        attribution="Routing data © OpenStreetMap contributors",
    )

    payload = _build_agent_payload(route)

    assert payload["geometry"] == [
        {"latitude": 22.5, "longitude": 113.9},
        {"latitude": 22.6, "longitude": 114.0},
    ]
    assert payload["geometry_point_count"] == 2
    assert payload["step_count"] == 1
    assert payload["steps"][0]["road_name"] == "测试路"


@pytest.mark.asyncio
async def test_plan_driving_route_returns_user_safe_coordinate_error() -> None:
    result = await plan_driving_route(
        origin_latitude=91,
        origin_longitude=113.9,
        destination_latitude=22.6,
        destination_longitude=114.0,
    )

    payload = _parse_text_result(result)
    assert payload["ok"] is False
    assert "纬度必须在 -90 到 90 之间" in payload["error"]
    assert "pydantic.dev" not in payload["error"]


@pytest.mark.asyncio
async def test_plan_walking_route_returns_user_safe_coordinate_error() -> None:
    result = await plan_walking_route(
        origin_latitude=91,
        origin_longitude=113.9,
        destination_latitude=22.6,
        destination_longitude=114.0,
    )

    payload = _parse_text_result(result)
    assert payload["ok"] is False
    assert "纬度必须在 -90 到 90 之间" in payload["error"]
    assert "pydantic.dev" not in payload["error"]


@pytest.mark.asyncio
async def test_plan_transit_route_returns_user_safe_coordinate_error() -> None:
    result = await plan_transit_route(
        origin_latitude=91,
        origin_longitude=113.9,
        destination_latitude=22.6,
        destination_longitude=114.0,
    )

    payload = _parse_text_result(result)
    assert payload["ok"] is False
    assert "纬度必须在 -90 到 90 之间" in payload["error"]
    assert "pydantic.dev" not in payload["error"]
