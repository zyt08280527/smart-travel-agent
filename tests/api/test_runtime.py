from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from langchain.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from travel_agent.api.runtime import (
    AgentRuntime,
    AgentRuntimeError,
    AgentRuntimeNotStartedError,
    ConversationNotFoundError,
    PendingApprovalNotFoundError,
)
from travel_agent.api.schemas import ApprovalRequest
from travel_agent.services.conversation import ConversationRepository

THREAD_ID = UUID("12345678-1234-5678-1234-567812345678")


def conversation_repository(tmp_path: Path) -> ConversationRepository:
    """Give each runtime test an isolated product-history database."""
    return ConversationRepository(tmp_path / "conversations.sqlite")

INTERRUPT_VALUE = {
    "action_requests": [
        {
            "name": "save_itinerary",
            "args": {"title": "测试行程"},
            "description": "请确认是否保存以下行程。",
        }
    ],
    "review_configs": [
        {
            "action_name": "save_itinerary",
            "allowed_decisions": ["approve", "reject"],
        }
    ],
}


class FakeAgent:
    """Return one interrupt, then one completed answer."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    async def ainvoke(
        self,
        input_value: object,
        config: dict[str, object],
    ) -> dict[str, Any]:
        self.calls.append((input_value, config))
        if len(self.calls) == 1:
            return {
                "messages": [AIMessage(content="", tool_calls=[])],
                "__interrupt__": (
                    SimpleNamespace(value=INTERRUPT_VALUE),
                ),
            }
        return {
            "messages": [AIMessage(content="行程已成功保存。")],
        }

    async def aget_state(self, _config: dict[str, object]) -> Any:
        return SimpleNamespace(interrupts=())


class FakeCheckpointer:
    """Record checkpoint deletion requests made by the runtime."""

    def __init__(self) -> None:
        self.deleted_thread_ids: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_thread_ids.append(thread_id)


class DeletableFakeAgent(FakeAgent):
    """Expose the checkpointer interface used by production deletion."""

    def __init__(self) -> None:
        super().__init__()
        self.checkpointer = FakeCheckpointer()


class RecoveringFakeAgent:
    """Expose one persisted interrupt after a simulated runtime restart."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    async def ainvoke(
        self,
        input_value: object,
        config: dict[str, object],
    ) -> dict[str, Any]:
        self.calls.append((input_value, config))
        return {"messages": [AIMessage(content="行程已成功保存。")]}

    async def aget_state(self, _config: dict[str, object]) -> Any:
        return SimpleNamespace(
            interrupts=(SimpleNamespace(value=INTERRUPT_VALUE),)
        )


class StreamingFakeAgent:
    """Emit deterministic v2 stream parts and one completed snapshot."""

    async def astream(
        self,
        _input_value: object,
        config: dict[str, object],
        **_kwargs: object,
    ) -> Any:
        del config
        yield {
            "type": "updates",
            "data": {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "query_current_weather",
                                    "args": {"city": "深圳"},
                                    "id": "call_weather",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            },
        }
        yield {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="天气结果",
                            name="query_current_weather",
                            tool_call_id="call_weather",
                        )
                    ]
                }
            },
        }
        yield {
            "type": "messages",
            "data": (
                AIMessageChunk(content="深圳天气"),
                {"langgraph_node": "model"},
            ),
        }

    async def aget_state(self, _config: dict[str, object]) -> Any:
        return SimpleNamespace(
            interrupts=(),
            values={"messages": [AIMessage(content="深圳天气很好。")]},
        )


class InterruptingStreamingFakeAgent:
    """Pause a streamed save request and complete after a decision."""

    def __init__(self) -> None:
        self.resume_calls: list[object] = []
        self.interrupted = False

    async def astream(
        self,
        _input_value: object,
        config: dict[str, object],
        **_kwargs: object,
    ) -> Any:
        del config
        yield {
            "type": "updates",
            "data": {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "save_itinerary",
                                    "args": {"title": "流式审批测试"},
                                    "id": "call_save",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            },
        }
        self.interrupted = True

    async def aget_state(self, _config: dict[str, object]) -> Any:
        return SimpleNamespace(
            interrupts=(SimpleNamespace(value=INTERRUPT_VALUE),)
            if self.interrupted
            else (),
            values={"messages": []},
        )

    async def ainvoke(
        self,
        input_value: object,
        config: dict[str, object],
    ) -> dict[str, Any]:
        del config
        self.resume_calls.append(input_value)
        return {"messages": [AIMessage(content="用户已拒绝，行程未保存。")]}


@pytest.mark.asyncio
async def test_runtime_keeps_agent_alive_across_chat_and_approval(
    tmp_path: Path,
) -> None:
    fake_agent = FakeAgent()
    session_closed = False

    @asynccontextmanager
    async def fake_session() -> Any:
        nonlocal session_closed
        yield fake_agent, [SimpleNamespace(name="save_itinerary")]
        session_closed = True

    repository = conversation_repository(tmp_path)
    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=repository,
    )
    await runtime.start()

    interrupted = await runtime.chat("请保存测试行程", THREAD_ID)
    assert interrupted.status == "approval_required"
    assert interrupted.pending_actions[0].name == "save_itinerary"
    assert runtime.tool_names == ["save_itinerary"]

    completed = await runtime.decide(
        THREAD_ID,
        ApprovalRequest(decision="approve"),
    )
    assert completed.status == "completed"
    assert completed.answer == "行程已成功保存。"
    assert len(fake_agent.calls) == 2

    history = await repository.get_conversation(THREAD_ID)
    assert history is not None
    assert history.conversation.status == "ready"
    assert [message.role for message in history.messages] == [
        "user",
        "user",
        "assistant",
    ]
    assert history.messages[1].content == "已批准该操作。"

    await runtime.stop()
    assert session_closed is True


@pytest.mark.asyncio
async def test_runtime_rejects_approval_without_pending_action(
    tmp_path: Path,
) -> None:
    runtime = AgentRuntime(
        conversation_repository=conversation_repository(tmp_path)
    )

    with pytest.raises(AgentRuntimeNotStartedError):
        await runtime.chat("你好", THREAD_ID)

    @asynccontextmanager
    async def fake_session() -> Any:
        yield FakeAgent(), []

    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=conversation_repository(tmp_path),
    )
    await runtime.start()
    with pytest.raises(PendingApprovalNotFoundError, match="没有等待审批"):
        await runtime.decide(
            THREAD_ID,
            ApprovalRequest(decision="reject"),
        )
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_recovers_pending_approval_after_restart(
    tmp_path: Path,
) -> None:
    fake_agent = RecoveringFakeAgent()

    @asynccontextmanager
    async def fake_session() -> Any:
        yield fake_agent, [SimpleNamespace(name="save_itinerary")]

    repository = conversation_repository(tmp_path)
    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=repository,
    )
    await runtime.start()
    await repository.upsert_conversation(THREAD_ID, "重启前的保存请求")
    await repository.set_status(THREAD_ID, "approval_required")

    completed = await runtime.decide(
        THREAD_ID,
        ApprovalRequest(decision="approve"),
    )

    assert completed.status == "completed"
    assert completed.answer == "行程已成功保存。"
    assert len(fake_agent.calls) == 1
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_blocks_chat_for_recovered_pending_approval(
    tmp_path: Path,
) -> None:
    fake_agent = RecoveringFakeAgent()

    @asynccontextmanager
    async def fake_session() -> Any:
        yield fake_agent, [SimpleNamespace(name="save_itinerary")]

    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=conversation_repository(tmp_path),
    )
    await runtime.start()

    with pytest.raises(AgentRuntimeError, match="正在等待审批"):
        await runtime.chat("继续聊天", THREAD_ID)

    assert fake_agent.calls == []
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_streams_public_events_and_final_answer(
    tmp_path: Path,
) -> None:
    fake_agent = StreamingFakeAgent()

    @asynccontextmanager
    async def fake_session() -> Any:
        yield fake_agent, [SimpleNamespace(name="query_current_weather")]

    repository = conversation_repository(tmp_path)
    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=repository,
    )
    await runtime.start()

    events = [
        event async for event in runtime.stream_chat("深圳天气", THREAD_ID)
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_requested",
        "tool_completed",
        "assistant_delta",
        "final",
        "done",
    ]
    assert events[1].tool_args == {"city": "深圳"}
    assert events[2].tool_name == "query_current_weather"
    assert events[3].delta == "深圳天气"
    assert events[4].answer == "深圳天气很好。"
    history = await repository.get_conversation(THREAD_ID)
    assert history is not None
    assert [message.content for message in history.messages] == [
        "深圳天气",
        "深圳天气很好。",
    ]
    await runtime.stop()


def test_runtime_extracts_only_current_turn_local_replan_card() -> None:
    old_tool = ToolMessage(
        name="query_current_weather",
        tool_call_id="call-weather",
        content=(
            '{"location":{"name":"深圳"},"temperature_c":25,'
            '"apparent_temperature_c":26,"precipitation_mm":0,'
            '"wind_speed_kmh":5,"condition":"晴朗",'
            '"observed_at":"2026-09-12T12:00"}'
        ),
    )
    planning_payload = {
        "context": {
            "origin_name": "粤海校区",
            "destination_name": "丽湖校区",
            "preferences": {"priority": "balanced", "can_drive": False},
        },
        "recommendation": {
            "recommended_mode": "transit",
            "ranked_options": [
                {
                    "option": {"mode": "transit", "duration_s": 2700},
                    "scores": {"total": 84},
                }
            ],
            "unavailable_options": [],
        },
    }
    messages = [
        HumanMessage(content="深圳天气"),
        old_tool,
        AIMessage(content="天气回答"),
        HumanMessage(content="如果不能开车呢"),
        AIMessage(
            content="已重新规划",
            additional_kwargs={
                "travel_planning_payload": planning_payload,
                "travel_planning_update": {
                    "reused_previous_data": True,
                    "previous_recommended_mode": "driving",
                },
            },
        ),
    ]

    cards = AgentRuntime._cards_from_messages(messages)

    assert len(cards) == 1
    assert cards[0]["type"] == "planning"
    assert cards[0]["reused_previous_data"] is True


@pytest.mark.asyncio
async def test_runtime_streams_approval_and_can_resume_rejection(
    tmp_path: Path,
) -> None:
    fake_agent = InterruptingStreamingFakeAgent()

    @asynccontextmanager
    async def fake_session() -> Any:
        yield fake_agent, [SimpleNamespace(name="save_itinerary")]

    repository = conversation_repository(tmp_path)
    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=repository,
    )
    await runtime.start()

    events = [
        event async for event in runtime.stream_chat("保存行程", THREAD_ID)
    ]

    assert [event.type for event in events] == [
        "run_started",
        "tool_requested",
        "approval_required",
        "done",
    ]
    assert events[1].tool_name == "save_itinerary"
    assert events[2].pending_actions[0].name == "save_itinerary"
    interrupted_history = await repository.get_conversation(THREAD_ID)
    assert interrupted_history is not None
    assert interrupted_history.conversation.status == "approval_required"

    rejected = await runtime.decide(
        THREAD_ID,
        ApprovalRequest(decision="reject"),
    )
    assert rejected.status == "completed"
    assert rejected.answer == "用户已拒绝，行程未保存。"
    assert len(fake_agent.resume_calls) == 1
    completed_history = await repository.get_conversation(THREAD_ID)
    assert completed_history is not None
    assert completed_history.conversation.status == "ready"
    assert [message.role for message in completed_history.messages] == [
        "user",
        "user",
        "assistant",
    ]
    await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_deletes_checkpoint_and_product_history(
    tmp_path: Path,
) -> None:
    fake_agent = DeletableFakeAgent()

    @asynccontextmanager
    async def fake_session() -> Any:
        yield fake_agent, []

    repository = conversation_repository(tmp_path)
    runtime = AgentRuntime(
        session_factory=fake_session,
        conversation_repository=repository,
    )
    await runtime.start()
    await repository.upsert_conversation(THREAD_ID, "要删除的会话")
    await repository.append_message(THREAD_ID, "user", "测试消息")

    await runtime.delete_conversation(THREAD_ID)

    assert fake_agent.checkpointer.deleted_thread_ids == [str(THREAD_ID)]
    assert await repository.get_conversation(THREAD_ID) is None
    with pytest.raises(ConversationNotFoundError, match="会话不存在"):
        await runtime.delete_conversation(THREAD_ID)
    await runtime.stop()
