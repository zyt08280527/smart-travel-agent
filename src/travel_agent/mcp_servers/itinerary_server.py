import json
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from travel_agent.domain.itinerary import ItineraryDraft
from travel_agent.services.itinerary import (
    ItineraryService,
    ItineraryServiceError,
)

mcp = FastMCP("travel-itinerary")


@mcp.tool()
async def save_itinerary(
    title: str,
    origin: str,
    destination: str,
    travel_mode: Literal["driving", "walking", "transit"],
    distance_m: float,
    duration_s: float,
    duration_basis: Literal[
        "static_without_live_traffic",
        "traffic_aware_estimate",
    ],
    notes: str | None = None,
) -> str:
    """将已经规划好的行程保存到本地存储。

    这是会改变持久化状态的写入工具。Agent 运行时会在真正执行前拦截该调用，
    并请求用户批准；用户已经要求保存时，模型无需先用自然语言重复询问确认。

    Args:
        title: 用户可识别的行程标题。
        origin: 起点名称。
        destination: 终点名称。
        travel_mode: 出行方式，driving 表示驾车，walking 表示步行，
            transit 表示公共交通。
        distance_m: 路线距离，单位为米。
        duration_s: 路线预计时长，单位为秒。
        duration_basis: 时长依据。static_without_live_traffic 表示静态预计；
            traffic_aware_estimate 表示包含查询时交通状况的预计值。
        notes: 可选的行程备注。

    Returns:
        包含行程 ID、保存时间和完整行程内容的 JSON 字符串。
    """
    try:
        draft = ItineraryDraft(
            title=title,
            origin=origin,
            destination=destination,
            travel_mode=travel_mode,
            distance_m=distance_m,
            duration_s=duration_s,
            duration_basis=duration_basis,
            notes=notes,
        )
        saved = await ItineraryService().save(draft)
        return json.dumps(
            {
                "ok": True,
                "itinerary": saved.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ValidationError:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    "行程参数无效：标题、起点和终点不能为空，"
                    "距离与时长不得为负数，出行方式仅支持 "
                    "driving、walking 或 transit"
                ),
            },
            ensure_ascii=False,
        )
    except ItineraryServiceError as exc:
        return json.dumps(
            {"ok": False, "error": str(exc)},
            ensure_ascii=False,
        )


def main() -> None:
    # stdio is protocol traffic: never print ordinary logs to stdout here.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
