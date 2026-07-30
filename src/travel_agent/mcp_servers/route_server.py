import json
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import ValidationError

from travel_agent.domain.route import GeoPoint, RoutePlan
from travel_agent.observability.http import capture_external_http_requests
from travel_agent.observability.mcp import observed_text_result
from travel_agent.services.route import RouteService, RouteServiceError
from travel_agent.services.transit import TransitService, TransitServiceError
from travel_agent.services.walking_route import WalkingRouteService

mcp = FastMCP("travel-route")


def _build_agent_payload(route: RoutePlan) -> dict[str, object]:
    """Build a compact route payload for the language model."""
    payload = route.model_dump(exclude={"geometry"})
    payload["step_count"] = len(route.steps)
    payload["geometry_point_count"] = len(route.geometry)
    return payload


def _coordinate_error_json() -> str:
    return json.dumps(
        {
            "ok": False,
            "error": (
                "坐标参数无效：纬度必须在 -90 到 90 之间，"
                "经度必须在 -180 到 180 之间"
            ),
        },
        ensure_ascii=False,
    )


def _service_error_json(exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "error": str(exc)},
        ensure_ascii=False,
    )


@mcp.tool()
async def plan_driving_route(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> CallToolResult:
    """根据起点和终点的 WGS84 经纬度规划驾车路线。

    Args:
        origin_latitude: 起点纬度，取值 -90 到 90。
        origin_longitude: 起点经度，取值 -180 到 180。
        destination_latitude: 终点纬度，取值 -90 到 90。
        destination_longitude: 终点经度，取值 -180 到 180。

    Returns:
        包含驾车距离、静态预计时长、导航步骤数量、导航步骤、
        路线轨迹点数量和数据署名的 JSON 字符串。
        为减少模型上下文消耗，不返回完整路线轨迹坐标。
    """
    with capture_external_http_requests() as requests:
        try:
            origin = GeoPoint(
                latitude=origin_latitude,
                longitude=origin_longitude,
            )
            destination = GeoPoint(
                latitude=destination_latitude,
                longitude=destination_longitude,
            )
            result = await RouteService().plan_driving_route(origin, destination)
            text = json.dumps(
                _build_agent_payload(result),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except ValidationError:
            text = _coordinate_error_json()
        except RouteServiceError as exc:
            text = _service_error_json(exc)
    return observed_text_result(text, requests)


@mcp.tool()
async def plan_walking_route(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> CallToolResult:
    """根据起点和终点的 WGS84 经纬度规划步行路线。

    Args:
        origin_latitude: 起点纬度，取值 -90 到 90。
        origin_longitude: 起点经度，取值 -180 到 180。
        destination_latitude: 终点纬度，取值 -90 到 90。
        destination_longitude: 终点经度，取值 -180 到 180。

    Returns:
        包含步行距离、静态预计时长、导航步骤数量、中文导航步骤、
        路线轨迹点数量和数据署名的 JSON 字符串。
        为减少模型上下文消耗，不返回完整路线轨迹坐标。
    """
    with capture_external_http_requests() as requests:
        try:
            origin = GeoPoint(
                latitude=origin_latitude,
                longitude=origin_longitude,
            )
            destination = GeoPoint(
                latitude=destination_latitude,
                longitude=destination_longitude,
            )
            result = await WalkingRouteService().plan_walking_route(
                origin,
                destination,
            )
            text = json.dumps(
                _build_agent_payload(result),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except ValidationError:
            text = _coordinate_error_json()
        except RouteServiceError as exc:
            text = _service_error_json(exc)
    return observed_text_result(text, requests)


@mcp.tool()
async def plan_transit_route(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    strategy: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8] = 0,
    alternative_routes: Literal[1, 2, 3] = 3,
) -> CallToolResult:
    """根据WGS84起终点坐标规划公共交通路线。

    工具会自动把WGS84坐标转换为高德GCJ-02坐标，并自动解析起终点城市编码。

    Args:
        origin_latitude: 起点纬度，取值 -90 到 90。
        origin_longitude: 起点经度，取值 -180 到 180。
        destination_latitude: 终点纬度，取值 -90 到 90。
        destination_longitude: 终点经度，取值 -180 到 180。
        strategy: 公交换乘策略。0推荐，1最经济，2最少换乘，
            3最少步行，4最舒适，5不乘地铁，6地铁图，
            7地铁优先，8时间短。
        alternative_routes: 返回候选方案数量，取值1到3，默认为3。

    Returns:
        包含候选公交方案、总距离、预计时长、步行距离、费用、
        换乘次数、步行与乘车分段以及数据来源的JSON字符串。
    """
    with capture_external_http_requests() as requests:
        try:
            origin = GeoPoint(
                latitude=origin_latitude,
                longitude=origin_longitude,
            )
            destination = GeoPoint(
                latitude=destination_latitude,
                longitude=destination_longitude,
            )
            result = await TransitService().plan_transit_route(
                origin,
                destination,
                strategy=strategy,
                alternative_routes=alternative_routes,
            )
            text = result.model_dump_json()
        except ValidationError:
            text = _coordinate_error_json()
        except (ValueError, TransitServiceError) as exc:
            text = _service_error_json(exc)
    return observed_text_result(text, requests)


def main() -> None:
    # stdio is protocol traffic: never print ordinary logs to stdout here.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
