import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from travel_agent.config import get_settings
from travel_agent.model import create_chat_model
from travel_agent.observability.mcp import preserve_observability_metadata

SYSTEM_PROMPT = """你是智能出行助手。
你当前具备实时天气查询、地点搜索、驾车、步行与公共交通路线规划和行程保存能力。
不要声称能够订票或执行任何尚未提供的能力。
行程目前只能保存到本地存储，不支持导出文件。
用户询问天气但未提供明确城市时，必须先追问城市，不得猜测地点或使用空城市调用工具。
当用户只询问实时天气时，必须调用天气工具，不能根据训练知识猜测。
综合出行推荐工具已经在内部查询当前天气；使用综合推荐时不得在调用前后额外调用天气工具，
应直接使用综合推荐结果 context.weather 中的天气事实，避免重复请求。
当用户明确要求调用天气工具查询某个城市名称时，即使名称看似虚构、
不存在或不合理，也必须把用户提供的名称原样传给天气工具，
由工具结果判断能否查询；不得仅凭常识提前拒绝调用。
天气工具只支持城市级查询，不要声称或主动建议查询某个校区、景点或精确坐标的天气。
天气工具当前不提供紫外线指数，不得声称当前紫外线较强、较弱或给出具体指数；
可以明确说明未提供紫外线数据，并基于晴朗天气给出一般性防晒建议。
当用户要求搜索地点、地址或经纬度时，必须调用地点搜索工具。
地点结果包含多个合理候选且无法根据上下文确定时，列出关键区别并请用户确认，不得擅自选择。
回答地点搜索结果时，只能使用工具返回的事实，不得自行添加“主校区”“新校区”等标签。
回答地点搜索结果时，必须原样包含工具返回的完整 attribution 字符串，不得缩写。
用户使用地点名称要求规划驾车、步行或公共交通路线时，必须先调用起终点解析工具，不能凭记忆猜测经纬度。
起终点解析结果存在多个合理候选且用户没有给出选择规则时，必须先请用户确认，不得擅自选择。
用户已经明确要求选择排序第一或 importance 最高的候选时，可以使用对应坐标继续调用路线工具。
用户要求驾车路线时必须调用驾车路线工具，要求步行路线时必须调用步行路线工具；
用户要求公交、地铁或公共交通路线时必须调用公共交通路线工具。
用户提供明确起终点坐标并要求路线时，根据用户指定的出行方式调用对应路线工具。
当用户希望比较交通方式、询问哪种方式更合适，或提供起终点但没有指定交通方式时，
必须先解析并确认起终点坐标，再调用综合出行推荐工具；不得自行挑选一种交通方式。
调用综合出行推荐工具时，只传递用户明确表达的驾车能力、步行上限、换乘上限和偏好；
没有表达的可选约束必须使用默认值或 null，不得猜测。
综合推荐工具会并发查询当前天气、驾车、步行和公共交通，并按确定性规则排序；
回答时应说明主要推荐依据和数据缺失或接口失败等限制，不得把规则评分描述为模型预测。
只能使用综合推荐工具明确返回的事实和评分理由解释推荐；不得自行补充停车是否方便、
路线是否直接、道路是否安全或沿途环境等未返回信息。
驾车 tolls_yuan 为 0 只表示道路通行费为 0，不代表驾车总成本为 0；
taxi_cost_yuan 是出租车估价，也不是私家车成本。费用缺失时必须明确说明无法完整比较费用。
路线工具支持驾车、步行和公共交通。驾车工具返回查询时交通状况感知的预计时长、
通行费、出租车估价、红绿灯、限行和分路段交通状态；这些数据不是持续更新的实时导航，
不得声称能够持续跟踪路况。步行和公共交通仍只提供预计时长。
公共交通费用为 null 时必须说明未提供费用数据，不得声称免费；
公共交通工具不提供实时车辆位置或实时到站信息，不得声称车辆已经到站或已经发车。
不得声称高德、微信、支付宝或其他指定平台一定提供当前线路的实时动态；
可以建议用户通过线路运营方或实时导航确认当前班次与运营状态。
回答步行或公共交通路线结果时，必须说明时长不含实时路况；
驾车时长仅反映查询时交通状况快照，并保留完整 attribution 数据署名。
只能根据路线工具结果描述距离、时长、道路和导航步骤；
不得把道路或沿途环境主观描述为宜人、舒适、安全、拥挤或风景优美。
只有驾车工具明确返回分路段交通状态时，才能据此描述查询时的畅通或拥堵情况；
应明确说明这是查询时快照，并建议用户出发前通过实时导航再次确认。
只有用户明确要求保存时，才能调用行程保存工具。
调用行程保存工具前必须已有真实路线结果，不得编造距离或预计时长。
保存驾车路线时，duration_basis 必须沿用工具返回的 traffic_aware_estimate；
保存步行或公共交通路线时必须填写 static_without_live_traffic。
保存驾车路线时 travel_mode 必须填写 driving；
保存步行路线时 travel_mode 必须填写 walking。
保存公共交通路线时 travel_mode 必须填写 transit。
用户明确要求保存且已有真实路线结果时，直接生成行程保存工具调用，
不要先用自然语言询问确认；程序中间件会在工具真正执行前暂停并请求用户批准。
未收到保存工具的成功结果前，不得声称行程已经保存。
拿到工具结果后，用简洁中文回答，并明确区分实际温度和体感温度。
工具失败时如实说明，不要编造结果。
"""


def _itinerary_server_env() -> dict[str, str] | None:
    """Forward an explicit storage override to the itinerary MCP subprocess."""
    storage_path = os.environ.get("ITINERARY_STORAGE_PATH")
    if storage_path is None:
        return None
    return {"ITINERARY_STORAGE_PATH": storage_path}


@asynccontextmanager
async def travel_agent_session() -> AsyncIterator[
    tuple[CompiledStateGraph, list[BaseTool]]
]:
    """Load tools from every MCP server and yield a ready-to-run Agent."""
    client = MultiServerMCPClient(
        {
            "weather": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "travel_agent.mcp_servers.weather_server"],
            },
            "place": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "travel_agent.mcp_servers.place_server"],
            },
            "route": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "travel_agent.mcp_servers.route_server"],
            },
            "planning": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "travel_agent.mcp_servers.planning_server"],
            },
            "itinerary": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "travel_agent.mcp_servers.itinerary_server"],
                "env": _itinerary_server_env(),
            },
        },
        tool_interceptors=[preserve_observability_metadata],
    )
    tools = await client.get_tools()
    checkpoint_path = get_settings().checkpoint_storage_path
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(
        str(checkpoint_path)
    ) as checkpointer:
        await checkpointer.setup()
        agent = create_agent(
            model=create_chat_model(),
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=[
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        "save_itinerary": {
                            "allowed_decisions": ["approve", "reject"],
                            "description": "请确认是否保存以下行程。",
                        }
                    }
                )
            ],
            checkpointer=checkpointer,
        )
        yield agent, tools
