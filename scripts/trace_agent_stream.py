"""Trace LangGraph streaming events for one Agent request."""

import argparse
import asyncio
import json
from uuid import uuid4

from langchain.messages import AIMessage, ToolMessage

from travel_agent.agent import travel_agent_session
from travel_agent.presentation import render_user_response


def _shorten(value: object, limit: int = 500) -> str:
    """Convert a value to readable text without flooding the terminal."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...（已截断）"


def _text_from_content(content: object) -> str:
    """Extract text from either string or content-block message formats."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _print_message_event(data: object) -> None:
    """Print one token/message chunk produced by a model node."""
    if not isinstance(data, tuple) or len(data) != 2:
        print(f"[messages] 无法识别的数据: {_shorten(data)}")
        return

    message_chunk, metadata = data
    node = metadata.get("langgraph_node", "unknown") if isinstance(metadata, dict) else "unknown"
    text = _text_from_content(getattr(message_chunk, "content", ""))
    tool_call_chunks = getattr(message_chunk, "tool_call_chunks", None)

    if isinstance(message_chunk, ToolMessage) and text:
        print(
            f"[messages][节点={node}] 工具结果片段: "
            f"{_shorten(text)}"
        )
    elif text:
        print(f"[messages][节点={node}] 模型文本片段: {text!r}")
    if tool_call_chunks:
        print(
            f"[messages][节点={node}] 工具调用片段: "
            f"{_shorten(tool_call_chunks)}"
        )


def _print_update_message(message: object) -> None:
    """Print the important part of a completed graph-node message update."""
    if isinstance(message, ToolMessage):
        print(
            f"  工具执行完成: name={message.name}, "
            f"result={_shorten(message.content)}"
        )
    elif isinstance(message, AIMessage) and message.tool_calls:
        print(f"  模型形成完整工具请求: {_shorten(message.tool_calls)}")
    elif isinstance(message, AIMessage):
        text = _text_from_content(message.content)
        if text:
            print(f"  模型本轮完整文本: {_shorten(text)}")


def _print_update_event(data: object) -> None:
    """Print state changes emitted after one or more graph nodes finish."""
    if not isinstance(data, dict):
        print(f"[updates] 无法识别的数据: {_shorten(data)}")
        return

    for node, update in data.items():
        print(f"[updates] 节点完成: {node}")
        if not isinstance(update, dict):
            print(f"  更新内容: {_shorten(update)}")
            continue

        messages = update.get("messages", [])
        if not isinstance(messages, list):
            messages = [messages]
        for message in messages:
            _print_update_message(message)

        if "__interrupt__" in update:
            print(f"  工作流暂停: {_shorten(update['__interrupt__'])}")


async def main(user_message: str) -> None:
    """Run one request and print its streaming events plus final state."""
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    async with travel_agent_session() as (agent, tools):
        print(f"已加载 MCP 工具: {[tool.name for tool in tools]}")
        print(f"本次会话 ID: {thread_id}")
        print("\n========== LangGraph 流式事件 ==========\n")

        async for part in agent.astream(
            {"messages": [{"role": "user", "content": user_message}]},
            config=config,
            stream_mode=["updates", "messages"],
            version="v2",
        ):
            event_type = part.get("type")
            namespace = part.get("ns", ())
            print(f"\n事件类型: {event_type}, namespace: {namespace}")

            if event_type == "messages":
                _print_message_event(part.get("data"))
            elif event_type == "updates":
                _print_update_event(part.get("data"))
            else:
                print(f"事件数据: {_shorten(part.get('data'))}")

        snapshot = await agent.aget_state(config)
        messages = snapshot.values.get("messages", [])
        print("\n========== 用户可见最终响应 ==========\n")
        print(render_user_response(messages))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--message",
        default="深圳现在天气怎么样？请根据实时数据回答。",
        help="Message sent to the Agent.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.message))
