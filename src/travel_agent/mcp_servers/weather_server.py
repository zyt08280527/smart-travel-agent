import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from travel_agent.observability.http import (
    capture_external_http_requests,
    configure_safe_http_logging,
)
from travel_agent.observability.mcp import observed_text_result
from travel_agent.services.weather import WeatherService, WeatherServiceError

mcp = FastMCP("travel-weather")


@mcp.tool()
async def query_current_weather(city: str) -> CallToolResult:
    """查询指定城市的当前天气。

    仅用于单独的天气问题。用户要求比较交通方式、推荐出行方案或询问怎么去时，
    不要调用本工具；recommend_travel_plan 会在内部自行查询当前天气。

    Args:
        city: 中文或英文城市名，例如“深圳”或“Shanghai”。不要填写区县地址或景点名。

    Returns:
        包含地点、温度、体感温度、降水、风速和观测时间的 JSON 字符串。
    """
    with capture_external_http_requests() as requests:
        try:
            result = await WeatherService().get_current_weather(city)
            text = result.model_dump_json()
        except (ValueError, WeatherServiceError) as exc:
            text = json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            )
    return observed_text_result(text, requests)


def main() -> None:
    # stdio is protocol traffic: never print ordinary logs to stdout here.
    configure_safe_http_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
