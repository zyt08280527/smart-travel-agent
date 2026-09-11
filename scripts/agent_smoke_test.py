"""Run one Agent request and print the complete model/tool message trace."""

import argparse
import asyncio
import json
import sys
from uuid import uuid4

from langchain.messages import AIMessage, HumanMessage, ToolMessage

from travel_agent.agent import travel_agent_session
from travel_agent.presentation import render_user_response


def print_message_trace(messages: list[object]) -> None:
    """Print the important fields of each message in the Agent state."""
    print("\n========== Agent 消息轨迹 ==========")
    for index, message in enumerate(messages, start=1):
        print(f"\n[{index}] {type(message).__name__}")

        if isinstance(message, HumanMessage):
            print(f"用户内容: {message.content}")
        elif isinstance(message, AIMessage):
            if message.tool_calls:
                print("模型请求调用工具:")
                print(json.dumps(message.tool_calls, ensure_ascii=False, indent=2))
            else:
                print(f"模型回答: {message.content}")
        elif isinstance(message, ToolMessage):
            print(f"工具名称: {message.name}")
            print(f"对应调用 ID: {message.tool_call_id}")
            print(f"工具结果: {message.content}")


async def main(user_message: str) -> None:
    async with travel_agent_session() as (agent, tools):
        print(f"已加载 MCP 工具: {[tool.name for tool in tools]}")
        result = await agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": user_message,
                    }
                ]
            },
            config={"configurable": {"thread_id": str(uuid4())}},
        )

        messages = result["messages"]
        print_message_trace(messages)
        print("\n========== 用户可见最终响应 ==========\n")
        print(render_user_response(messages))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message",
        default="深圳现在热不热？请根据实时天气回答。",
        help="Message sent to the Agent.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.message))
