import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from travel_agent.observability.http import capture_external_http_requests
from travel_agent.observability.mcp import observed_text_result
from travel_agent.services.place import PlaceService, PlaceServiceError

mcp = FastMCP("travel-place")


@mcp.tool()
async def search_places(query: str, limit: int = 5) -> CallToolResult:
    """搜索地点名称或地址，返回候选地点及其经纬度。

    Args:
        query: 自由文本地点或地址，例如“深圳大学”或“深圳市南山区南海大道3688号”。
        limit: 返回的候选地点数量，取值 1 到 5，默认为 5。

    Returns:
        包含搜索词、候选地点、经纬度和数据来源署名的 JSON 字符串。
    """
    with capture_external_http_requests() as requests:
        try:
            result = await PlaceService().search_places(query, limit)
            text = result.model_dump_json()
        except (ValueError, PlaceServiceError) as exc:
            text = json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            )
    return observed_text_result(text, requests)


@mcp.tool()
async def resolve_route_endpoints(
    origin_query: str,
    destination_query: str,
    limit: int = 3,
) -> CallToolResult:
    """按顺序搜索路线的起点和终点，返回两组候选地点及经纬度。

    Args:
        origin_query: 起点名称或地址。
        destination_query: 终点名称或地址。
        limit: 每个地点返回的候选数量，取值 1 到 5，默认为 3。

    Returns:
        包含起点候选、终点候选、经纬度和数据来源署名的 JSON 字符串。
        两次外部搜索会按顺序执行并控制请求间隔。
    """
    with capture_external_http_requests() as requests:
        try:
            result = await PlaceService().resolve_route_endpoints(
                origin_query,
                destination_query,
                limit,
            )
            text = result.model_dump_json()
        except (ValueError, PlaceServiceError) as exc:
            text = json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            )
    return observed_text_result(text, requests)


def main() -> None:
    # stdio is protocol traffic: never print ordinary logs to stdout here.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
