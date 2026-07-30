"""Demonstrate planning and saving an itinerary with human approval."""

import argparse
import asyncio
import json
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from travel_agent.agent import travel_agent_session
from travel_agent.presentation import render_user_response

DEFAULT_MESSAGE = (
    "请规划从深圳大学粤海校区到深圳大学丽湖校区的驾车路线。"
    "如果起点和终点各只有一个候选，就直接使用。"
    "然后把这条行程保存为“深大两校区驾车行程”，"
    "备注静态预计时长不含实时路况。"
)


def print_interrupt(interrupt_value: dict[str, Any]) -> None:
    """Print the pending write action for human review."""
    print("\n========== 保存操作等待确认 ==========\n")
    print(json.dumps(interrupt_value, ensure_ascii=False, indent=2))


async def main(message: str) -> None:
    """Run until approval is required, then resume with the user's decision."""
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    async with travel_agent_session() as (agent, tools):
        print(f"已加载 MCP 工具: {[tool.name for tool in tools]}")
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
        )

        interrupts = result.get("__interrupt__", ())
        if not interrupts:
            print("\nAgent 没有发起需要确认的保存操作。")
            print(render_user_response(result["messages"]))
            return

        interrupt_value = interrupts[0].value
        print_interrupt(interrupt_value)
        answer = await asyncio.to_thread(
            input,
            "\n输入 y 批准保存，输入其他内容拒绝：",
        )
        if answer.strip().lower() == "y":
            decision: dict[str, str] = {"type": "approve"}
        else:
            decision = {
                "type": "reject",
                "message": "用户拒绝保存这条行程。",
            }

        resumed = await agent.ainvoke(
            Command(resume={"decisions": [decision]}),
            config=config,
        )
        print("\n========== 用户可见最终响应 ==========\n")
        print(render_user_response(resumed["messages"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args()
    asyncio.run(main(args.message))
