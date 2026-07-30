"""Evaluate the walking-route save workflow and its approval boundary."""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from langchain.messages import AIMessage, ToolMessage
from langgraph.types import Command

from travel_agent.agent import travel_agent_session
from travel_agent.presentation import render_user_response

EVAL_MESSAGE = (
    "请规划从深圳大学粤海校区到深圳大学丽湖校区的步行路线。"
    "如果起点和终点各只有一个候选就直接使用，"
    "最后将行程保存为深大两校区步行计划。"
)


class EvalFailure(AssertionError):
    """A human-readable walking HITL evaluation failure."""


def require(condition: bool, message: str) -> None:
    """Raise an evaluation failure when one expected behavior is absent."""
    if not condition:
        raise EvalFailure(message)


def save_result_succeeded(messages: list[object]) -> bool:
    """Return whether the resumed messages contain a successful save result."""
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "save_itinerary":
            continue
        blocks = (
            message.content if isinstance(message.content, list) else [message.content]
        )
        for block in blocks:
            text = block.get("text") if isinstance(block, dict) else block
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("ok") is True:
                return True
    return False


async def evaluate(storage_path: Path) -> None:
    """Run, approve, and verify one walking itinerary save workflow."""
    config = {"configurable": {"thread_id": str(uuid4())}}

    async with travel_agent_session() as (agent, tools):
        print(f"已加载 MCP 工具: {[tool.name for tool in tools]}")
        interrupted = await agent.ainvoke(
            {"messages": [{"role": "user", "content": EVAL_MESSAGE}]},
            config=config,
        )

        interrupts = interrupted.get("__interrupt__", ())
        require(bool(interrupts), "步行行程保存前必须产生 __interrupt__")
        request = interrupts[0].value
        action_requests = request.get("action_requests", [])
        require(len(action_requests) == 1, "应有且仅有一个待审批操作")

        pending_action = action_requests[0]
        print("\n待审批操作：")
        print(json.dumps(pending_action, ensure_ascii=False, indent=2))
        require(
            pending_action.get("name") == "save_itinerary",
            "待审批操作必须是 save_itinerary",
        )

        tool_names = [
            tool_call["name"]
            for message in interrupted["messages"]
            if isinstance(message, AIMessage)
            for tool_call in message.tool_calls
        ]
        expected_sequence = (
            "resolve_route_endpoints",
            "plan_walking_route",
            "save_itinerary",
        )
        require(
            tuple(tool_names) == expected_sequence,
            f"工具调用顺序应为 {expected_sequence!r}，实际为 {tuple(tool_names)!r}",
        )

        pending_args = pending_action["args"]
        require(
            pending_args.get("travel_mode") == "walking",
            "步行行程的 travel_mode 必须是 walking",
        )
        require(
            pending_args.get("duration_basis")
            == "static_without_live_traffic",
            "步行行程必须标记为静态预计时长",
        )
        print("[PASS] walking_route_and_save_are_orchestrated")

        exists_before_approval = await asyncio.to_thread(storage_path.exists)
        require(not exists_before_approval, "批准前不应创建行程文件")
        print("[PASS] walking_save_is_interrupted_before_write")

        resumed = await agent.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}),
            config=config,
        )

        exists_after_approval = await asyncio.to_thread(storage_path.exists)
        require(exists_after_approval, "批准后应创建行程文件")
        saved_text = await asyncio.to_thread(
            storage_path.read_text,
            encoding="utf-8",
        )
        lines = saved_text.splitlines()
        require(len(lines) == 1, "批准一次应只保存一条步行行程")

        saved = json.loads(lines[0])
        for field in (
            "title",
            "origin",
            "destination",
            "travel_mode",
            "distance_m",
            "duration_s",
            "duration_basis",
            "notes",
        ):
            require(
                saved.get(field) == pending_args.get(field),
                f"保存字段 {field!r} 必须与审批参数一致",
            )
        require(bool(saved.get("itinerary_id")), "保存记录应包含 itinerary_id")
        require(bool(saved.get("saved_at")), "保存记录应包含 saved_at")
        require(
            save_result_succeeded(resumed["messages"]),
            "保存工具结果应包含 ok=true",
        )
        print("[PASS] approved_walking_action_is_written_once")

        final_answer = render_user_response(resumed["messages"])
        print(f"\n最终回答：\n{final_answer}\n")
        require("保存" in final_answer, "最终回答应提及保存结果")
        require(
            "openrouteservice.org | OpenStreetMap contributors" in final_answer,
            "最终回答应保留步行路线数据署名",
        )
        require(
            "环境较宜人" not in final_answer,
            "最终回答不应添加工具未提供的主观环境判断",
        )
        print("[PASS] walking_final_answer_respects_data_boundaries")


async def main() -> None:
    """Evaluate with isolated temporary storage."""
    previous_path = os.environ.get("ITINERARY_STORAGE_PATH")
    try:
        with TemporaryDirectory(
            prefix="smart-travel-agent-walking-hitl-"
        ) as temp_dir:
            storage_path = Path(temp_dir) / "itineraries.jsonl"
            os.environ["ITINERARY_STORAGE_PATH"] = str(storage_path)
            await evaluate(storage_path)
    finally:
        if previous_path is None:
            os.environ.pop("ITINERARY_STORAGE_PATH", None)
        else:
            os.environ["ITINERARY_STORAGE_PATH"] = previous_path

    print("\n评估结果: 4/4 通过")


if __name__ == "__main__":
    asyncio.run(main())
