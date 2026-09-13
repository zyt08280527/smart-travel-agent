import json
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import ValidationError

from travel_agent.config import get_settings
from travel_agent.domain.decision import TravelPreferences
from travel_agent.domain.route import GeoPoint
from travel_agent.observability.http import (
    capture_external_http_requests,
    configure_safe_http_logging,
)
from travel_agent.observability.mcp import observed_text_result
from travel_agent.services.departure_time_parser import DepartureTimeParser
from travel_agent.services.travel_decision import TravelDecisionError
from travel_agent.services.travel_planning import TravelPlanningService

mcp = FastMCP("travel-planning")


def _error_json(message: str) -> str:
    return json.dumps(
        {"ok": False, "error": message},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _now(timezone: str) -> datetime:
    """Read the clock behind a small seam so time parsing remains testable."""
    return datetime.now(ZoneInfo(timezone))


@mcp.tool()
async def recommend_travel_plan(
    city: str,
    origin_name: str,
    destination_name: str,
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
    priority: Literal[
        "balanced",
        "fastest",
        "cheapest",
        "least_walking",
        "fewest_transfers",
    ] = "balanced",
    can_drive: bool | None = None,
    max_walking_distance_m: float | None = None,
    max_transfer_count: int | None = None,
    departure_time_text: str | None = None,
) -> CallToolResult:
    """综合出发时段天气和多种路线，为一次出行推荐交通方式。

    仅在用户希望比较交通方式、请求出行建议，或没有指定交通方式时使用。
    调用前必须先通过地点工具取得明确的起终点坐标。
    本工具会自行查询当前天气或未来逐小时预报；调用前后都不要再调用
    query_current_weather。用户表达出发时间时，将其原话完整传入，不要自行改写
    为猜测的日期时间；只提供日期而没有时段时，本工具会要求补充信息。
    本工具已经返回三种交通方式的比较结果；成功调用后直接回答，不要继续调用
    plan_driving_route、plan_walking_route 或 plan_transit_route。

    Args:
        city: 天气查询使用的城市名称。
        origin_name: 已确认的起点名称。
        destination_name: 已确认的终点名称。
        origin_latitude: 起点WGS84纬度。
        origin_longitude: 起点WGS84经度。
        destination_latitude: 终点WGS84纬度。
        destination_longitude: 终点WGS84经度。
        priority: 用户偏好，依次支持综合、最快、最便宜、少步行和少换乘。
        can_drive: 用户能否驾车；未知时传null。
        max_walking_distance_m: 用户可接受的最大步行距离；未说明时传null。
        max_transfer_count: 用户可接受的最大换乘次数；未说明时传null。
        departure_time_text: 用户关于出发时间的原始表达；未说明时传null。

    Returns:
        包含当前或预报天气、候选路线、可解释评分、推荐方式、失败降级信息和
        数据署名的JSON字符串。路线是查询时快照，不代表未来时段实时导航。
    """
    with capture_external_http_requests() as requests:
        try:
            settings = get_settings()
            reference_at = _now(settings.business_timezone)
            departure_time = DepartureTimeParser(
                settings.business_timezone
            ).parse(
                departure_time_text or "",
                reference_at=reference_at,
            )
            if departure_time is not None and departure_time.precision == "date_only":
                text = _error_json(
                    "只识别到出发日期，请补充上午、下午、晚上或具体时间"
                )
                return observed_text_result(text, requests)
            origin = GeoPoint(
                latitude=origin_latitude,
                longitude=origin_longitude,
            )
            destination = GeoPoint(
                latitude=destination_latitude,
                longitude=destination_longitude,
            )
            preferences = TravelPreferences(
                priority=priority,
                can_drive=can_drive,
                max_walking_distance_m=max_walking_distance_m,
                max_transfer_count=max_transfer_count,
            )
            result = await TravelPlanningService().compare(
                city=city,
                origin_name=origin_name,
                destination_name=destination_name,
                origin=origin,
                destination=destination,
                preferences=preferences,
                departure_time=departure_time,
                reference_at=reference_at,
            )
            text = result.model_dump_json(exclude_none=True)
        except ValidationError:
            text = _error_json(
                "出行偏好或坐标参数无效，请检查城市、起终点、经纬度及约束"
            )
        except (TravelDecisionError, ValueError) as exc:
            text = _error_json(str(exc))
    return observed_text_result(text, requests)


def main() -> None:
    # stdio is protocol traffic: never print ordinary logs to stdout here.
    configure_safe_http_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
