"""Evaluate that itinerary writes are interrupted before approval."""

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
    "请查询深圳当前天气，并规划从深圳大学粤海校区到深圳大学丽湖校区的驾车路线。"
    "如果起点和终点各只有一个候选，就直接使用。"
    "请结合实时天气给出出行建议，最后将行程保存为深大两校区天气出行计划，"
    "备注中包含天气与出行建议。"
)
class EvalFailure(AssertionError):
    """A human-readable HITL evaluation failure."""


def require(condition: bool, message: str) -> None:
    """Raise an evaluation failure when one expected behavior is absent."""
    if not condition:
        raise EvalFailure(message)


def tool_payloads(
    messages: list[object],
    tool_name: str,
) -> list[dict[str, object]]:
    """Parse JSON payloads returned by one named tool."""
    payloads: list[dict[str, object]] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != tool_name:
            continue
        content_blocks = (
            message.content if isinstance(message.content, list) else [message.content]
        )
        for block in content_blocks:
            text = block.get("text") if isinstance(block, dict) else block
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


async def evaluate(storage_path: Path) -> None:
    """Run one interrupted Agent task and approve its pending save action."""
    config = {"configurable": {"thread_id": str(uuid4())}}

    async with travel_agent_session() as (agent, tools):
        print(f"已加载 MCP 工具: {[tool.name for tool in tools]}")
        interrupted = await agent.ainvoke(
            {"messages": [{"role": "user", "content": EVAL_MESSAGE}]},
            config=config,
        )

        interrupts = interrupted.get("__interrupt__", ())
        require(bool(interrupts), "保存操作执行前必须产生 __interrupt__")
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
        for required_tool in (
            "query_current_weather",
            "resolve_route_endpoints",
            "plan_driving_route",
            "save_itinerary",
        ):
            require(
                required_tool in tool_names,
                f"端到端任务应调用 {required_tool}",
            )
        require(
            tool_names.index("resolve_route_endpoints")
            < tool_names.index("plan_driving_route")
            < tool_names.index("save_itinerary"),
            "地点解析、路线规划和保存必须按依赖顺序执行",
        )
        require(
            tool_names.index("query_current_weather")
            < tool_names.index("save_itinerary"),
            "保存天气出行计划前必须先查询天气",
        )

        pending_notes = str(pending_action["args"].get("notes", ""))
        require("天气" in pending_notes, "待保存备注应包含天气信息")
        require(
            pending_action["args"].get("duration_basis")
            == "traffic_aware_estimate",
            "驾车行程必须沿用交通感知预计时长依据",
        )
        print("[PASS] weather_place_route_and_save_are_orchestrated")

        exists_before_approval = await asyncio.to_thread(storage_path.exists)
        require(not exists_before_approval, "批准前不应创建行程文件")
        print("[PASS] save_is_interrupted_before_write")

        resumed = await agent.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}),
            config=config,
        )

        exists_after_approval = await asyncio.to_thread(storage_path.exists)
        require(exists_after_approval, "批准后应创建行程文件")
        saved_text = await asyncio.to_thread(storage_path.read_text, encoding="utf-8")
        lines = saved_text.splitlines()
        require(len(lines) == 1, "批准一次应只保存一条行程")

        saved = json.loads(lines[0])
        pending_args = pending_action["args"]
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

        save_result_payloads = tool_payloads(
            resumed["messages"],
            "save_itinerary",
        )
        require(
            any(payload.get("ok") is True for payload in save_result_payloads),
            "保存工具结果应包含 ok=true",
        )
        print("[PASS] approved_action_is_written_once")

        final_answer = render_user_response(resumed["messages"])
        print(f"\n最终回答：\n{final_answer}\n")
        require(
            "保存" in final_answer,
            "最终回答应提及保存结果",
        )
        require(
            not any(
                phrase in final_answer
                for phrase in ("保存失败", "未保存", "拒绝保存")
            ),
            "保存成功后最终回答不应表达相反结果",
        )
        require(
            "导出行程" not in final_answer or "暂不支持导出行程" in final_answer,
            "最终回答不应承诺导出行程",
        )
        require(
            not any(
                phrase in final_answer
                for phrase in (
                    "紫外线较强",
                    "紫外线很强",
                    "紫外线强烈",
                    "紫外线较弱",
                )
            ),
            "最终回答不应包含工具未提供的紫外线判断",
        )
        print("[PASS] final_answer_respects_data_boundaries")


async def main() -> None:
    """Use isolated temporary storage and print a compact evaluation report."""
    previous_path = os.environ.get("ITINERARY_STORAGE_PATH")
    try:
        with TemporaryDirectory(prefix="smart-travel-agent-hitl-") as temp_dir:
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
