"""Show hidden HTTP observations crossing the MCP process boundary."""

import asyncio
import json

from langchain.messages import ToolMessage

from travel_agent.agent import travel_agent_session


async def main() -> None:
    """Call the weather tool directly and print content versus artifact."""
    async with travel_agent_session() as (_agent, tools):
        weather_tool = next(
            tool for tool in tools if tool.name == "query_current_weather"
        )
        result = await weather_tool.ainvoke(
            {
                "type": "tool_call",
                "name": "query_current_weather",
                "args": {"city": "深圳"},
                "id": "weather-observability-smoke-test",
            }
        )

    if not isinstance(result, ToolMessage):
        raise TypeError("天气工具应返回包含 artifact 的 ToolMessage")

    print("\n========== 模型可见的业务内容 ==========")
    print(result.content)
    print("\n========== 应用可见、模型不可见的 artifact ==========")
    print(json.dumps(result.artifact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
