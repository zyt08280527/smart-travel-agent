import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID, uuid4

from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from travel_agent.agent import travel_agent_session
from travel_agent.api.schemas import (
    AgentResponse,
    AgentStreamEvent,
    ApprovalRequest,
    PendingAction,
)
from travel_agent.api.streaming import (
    normalize_stream_part,
    result_card_from_ai_message,
    result_card_from_tool_message,
)
from travel_agent.domain.conversation import (
    ConversationDetail,
    ConversationSummary,
)
from travel_agent.presentation import render_user_response
from travel_agent.services.conversation import ConversationRepository

AgentSession = AbstractAsyncContextManager[
    tuple[CompiledStateGraph, list[BaseTool]]
]
AgentSessionFactory = Callable[[], AgentSession]


class AgentRuntimeError(RuntimeError):
    """Base error for API-side Agent lifecycle failures."""


class AgentRuntimeNotStartedError(AgentRuntimeError):
    """Raised when the runtime is used before application startup."""


class PendingApprovalNotFoundError(AgentRuntimeError):
    """Raised when a thread has no interrupted action to resume."""


class ConversationNotFoundError(AgentRuntimeError):
    """Raised when product-facing conversation history does not exist."""


class AgentRuntime:
    """Keep one Agent session alive across chat and approval requests."""

    def __init__(
        self,
        session_factory: AgentSessionFactory = travel_agent_session,
        conversation_repository: ConversationRepository | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._session: AgentSession | None = None
        self._agent: CompiledStateGraph | None = None
        self._tools: list[BaseTool] = []
        self._pending_action_counts: dict[UUID, int] = {}
        self._conversation_repository = (
            conversation_repository or ConversationRepository()
        )

    @property
    def tool_names(self) -> list[str]:
        """Return the tools loaded from MCP servers."""
        return [tool.name for tool in self._tools]

    async def list_conversations(
        self,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        """Return the most recently active product conversations."""
        return await self._conversation_repository.list_conversations(limit)

    async def get_conversation(self, thread_id: UUID) -> ConversationDetail:
        """Return visible messages and cards for one conversation."""
        conversation = await self._conversation_repository.get_conversation(
            thread_id
        )
        if conversation is None:
            raise ConversationNotFoundError("会话不存在")
        return conversation

    async def delete_conversation(self, thread_id: UUID) -> None:
        """Delete both LangGraph execution state and visible history."""
        agent = self._require_agent()
        conversation = await self._conversation_repository.get_conversation(
            thread_id
        )
        if conversation is None:
            raise ConversationNotFoundError("会话不存在")

        checkpointer = getattr(agent, "checkpointer", None)
        delete_thread = getattr(checkpointer, "adelete_thread", None)
        if delete_thread is None:
            raise AgentRuntimeError("当前 Agent 检查点不支持删除")

        await delete_thread(str(thread_id))
        deleted = await self._conversation_repository.delete_conversation(
            thread_id
        )
        if not deleted:
            raise ConversationNotFoundError("会话不存在")
        self._pending_action_counts.pop(thread_id, None)

    async def start(self) -> None:
        """Enter one long-lived MCP and Agent session."""
        if self._session is not None:
            return
        await self._conversation_repository.initialize()
        session = self._session_factory()
        agent, tools = await session.__aenter__()
        self._session = session
        self._agent = agent
        self._tools = tools

    async def stop(self) -> None:
        """Close MCP subprocesses and release the Agent session."""
        session = self._session
        self._session = None
        self._agent = None
        self._tools = []
        self._pending_action_counts.clear()
        if session is not None:
            await session.__aexit__(None, None, None)

    async def chat(
        self,
        message: str,
        thread_id: UUID | None = None,
    ) -> AgentResponse:
        """Send one user message using a new or existing thread."""
        agent = self._require_agent()
        current_thread_id = thread_id or uuid4()
        action_count = self._pending_action_counts.get(current_thread_id)
        if action_count is None and thread_id is not None:
            action_count = await self._recover_pending_action_count(
                agent,
                current_thread_id,
            )
            if action_count is not None:
                self._pending_action_counts[current_thread_id] = action_count
        if action_count is not None:
            raise AgentRuntimeError("该任务正在等待审批，不能继续发送消息")

        await self._store_user_message(current_thread_id, message)
        try:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": message}]},
                config=self._config(current_thread_id),
            )
            response = self._build_response(current_thread_id, result)
            await self._store_response(response, result.get("messages", []))
            return response
        except Exception:
            await self._conversation_repository.set_status(
                current_thread_id,
                "error",
            )
            raise

    async def decide(
        self,
        thread_id: UUID,
        request: ApprovalRequest,
    ) -> AgentResponse:
        """Approve or reject every pending action in one interrupt."""
        agent = self._require_agent()
        action_count = self._pending_action_counts.pop(thread_id, None)
        if action_count is None:
            action_count = await self._recover_pending_action_count(
                agent,
                thread_id,
            )
        if action_count is None:
            raise PendingApprovalNotFoundError(
                "该任务没有等待审批的操作，可能已处理或不存在"
            )

        if request.decision == "approve":
            decision: dict[str, str] = {"type": "approve"}
            decision_message = "已批准该操作。"
        else:
            decision = {
                "type": "reject",
                "message": request.message or "用户拒绝执行该操作。",
            }
            decision_message = request.message or "已拒绝该操作。"

        await self._conversation_repository.append_message(
            thread_id,
            "user",
            decision_message,
        )
        try:
            result = await agent.ainvoke(
                Command(
                    resume={
                        "decisions": [
                            decision.copy() for _ in range(action_count)
                        ]
                    }
                ),
                config=self._config(thread_id),
            )
        except Exception:
            self._pending_action_counts[thread_id] = action_count
            await self._conversation_repository.set_status(thread_id, "error")
            raise
        response = self._build_response(thread_id, result)
        if (
            request.decision == "approve"
            and response.status == "completed"
            and self._save_itinerary_succeeded(result.get("messages", []))
        ):
            response = response.model_copy(
                update={
                    "answer": (
                        "行程已保存成功，可在左侧“已保存行程”中查看。"
                    )
                }
            )
        await self._store_response(response, result.get("messages", []))
        return response

    async def stream_chat(
        self,
        message: str,
        thread_id: UUID | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Stream one chat turn using the public application event protocol."""
        current_thread_id = thread_id or uuid4()
        yield AgentStreamEvent(
            type="run_started",
            thread_id=current_thread_id,
        )

        try:
            agent = self._require_agent()
            action_count = self._pending_action_counts.get(current_thread_id)
            if action_count is None and thread_id is not None:
                action_count = await self._recover_pending_action_count(
                    agent,
                    current_thread_id,
                )
                if action_count is not None:
                    self._pending_action_counts[current_thread_id] = (
                        action_count
                    )
            if action_count is not None:
                raise AgentRuntimeError(
                    "该任务正在等待审批，不能继续发送消息"
                )

            await self._store_user_message(current_thread_id, message)
            result_cards: list[dict[str, Any]] = []
            config = self._config(current_thread_id)
            async for part in agent.astream(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                stream_mode=["updates", "messages"],
                version="v2",
            ):
                for event in normalize_stream_part(part, current_thread_id):
                    if event.type == "result_card" and event.card is not None:
                        result_cards.append(event.card.model_dump(mode="json"))
                    yield event

            snapshot = await agent.aget_state(config)
            if snapshot.interrupts:
                pending_actions: list[PendingAction] = []
                for interrupt in snapshot.interrupts:
                    pending_actions.extend(
                        self._parse_pending_actions(interrupt.value)
                    )
                self._pending_action_counts[current_thread_id] = len(
                    pending_actions
                )
                await self._conversation_repository.set_status(
                    current_thread_id,
                    "approval_required",
                )
                yield AgentStreamEvent(
                    type="approval_required",
                    thread_id=current_thread_id,
                    pending_actions=pending_actions,
                )
            else:
                self._pending_action_counts.pop(current_thread_id, None)
                answer = render_user_response(snapshot.values["messages"])
                await self._conversation_repository.append_message(
                    current_thread_id,
                    "assistant",
                    answer,
                    result_cards,
                )
                await self._conversation_repository.set_status(
                    current_thread_id,
                    "ready",
                )
                yield AgentStreamEvent(
                    type="final",
                    thread_id=current_thread_id,
                    answer=answer,
                )
        except AgentRuntimeError as exc:
            yield AgentStreamEvent(
                type="error",
                thread_id=current_thread_id,
                error=str(exc),
            )
        except Exception:
            await self._mark_conversation_error(current_thread_id)
            yield AgentStreamEvent(
                type="error",
                thread_id=current_thread_id,
                error="Agent执行失败，请稍后重试",
            )

        yield AgentStreamEvent(
            type="done",
            thread_id=current_thread_id,
        )

    async def _store_user_message(
        self,
        thread_id: UUID,
        message: str,
    ) -> None:
        """Create or touch a conversation, then append the user message."""
        await self._conversation_repository.upsert_conversation(
            thread_id,
            message,
        )
        await self._conversation_repository.append_message(
            thread_id,
            "user",
            message,
        )

    async def _store_response(
        self,
        response: AgentResponse,
        messages: object,
    ) -> None:
        """Persist only completed visible answers; interruptions store status."""
        if response.status == "approval_required":
            await self._conversation_repository.set_status(
                response.thread_id,
                "approval_required",
            )
            return

        cards = self._cards_from_messages(messages)
        await self._conversation_repository.append_message(
            response.thread_id,
            "assistant",
            response.answer or "",
            cards,
        )
        await self._conversation_repository.set_status(
            response.thread_id,
            "ready",
        )

    async def _mark_conversation_error(self, thread_id: UUID) -> None:
        """Best-effort status update for failures after a turn was stored."""
        conversation = await self._conversation_repository.get_conversation(
            thread_id
        )
        if conversation is not None:
            await self._conversation_repository.set_status(thread_id, "error")

    @staticmethod
    def _cards_from_messages(messages: object) -> list[dict[str, Any]]:
        """Extract the same structured cards used by the streaming API."""
        if not isinstance(messages, list):
            return []
        latest_human_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage)
            ),
            -1,
        )
        cards: list[dict[str, Any]] = []
        for message in messages[latest_human_index + 1 :]:
            card = None
            if isinstance(message, ToolMessage):
                card = result_card_from_tool_message(message)
            elif isinstance(message, AIMessage):
                card = result_card_from_ai_message(message)
            if card is not None:
                cards.append(card.model_dump(mode="json"))
        return cards

    @staticmethod
    def _save_itinerary_succeeded(messages: object) -> bool:
        """Return true only when the save tool confirms a persisted record."""
        if not isinstance(messages, list):
            return False
        for message in reversed(messages):
            if not isinstance(message, ToolMessage):
                continue
            if getattr(message, "name", None) != "save_itinerary":
                continue
            try:
                payload = json.loads(str(message.content))
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            return bool(
                isinstance(payload, dict)
                and payload.get("ok") is True
                and isinstance(payload.get("itinerary"), dict)
                and payload["itinerary"].get("itinerary_id")
            )
        return False

    async def _recover_pending_action_count(
        self,
        agent: CompiledStateGraph,
        thread_id: UUID,
    ) -> int | None:
        """Recover pending HITL action count from a persisted checkpoint."""
        snapshot = await agent.aget_state(self._config(thread_id))
        action_count = 0
        for interrupt in snapshot.interrupts:
            action_count += len(self._parse_pending_actions(interrupt.value))
        return action_count or None

    def _require_agent(self) -> CompiledStateGraph:
        if self._agent is None:
            raise AgentRuntimeNotStartedError("Agent运行时尚未启动")
        return self._agent

    @staticmethod
    def _config(thread_id: UUID) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": str(thread_id)}}

    def _build_response(
        self,
        thread_id: UUID,
        result: dict[str, Any],
    ) -> AgentResponse:
        interrupts = result.get("__interrupt__", ())
        if interrupts:
            pending_actions = self._parse_pending_actions(
                interrupts[0].value
            )
            self._pending_action_counts[thread_id] = len(pending_actions)
            return AgentResponse(
                status="approval_required",
                thread_id=thread_id,
                pending_actions=pending_actions,
            )

        self._pending_action_counts.pop(thread_id, None)
        return AgentResponse(
            status="completed",
            thread_id=thread_id,
            answer=render_user_response(result["messages"]),
        )

    @staticmethod
    def _parse_pending_actions(
        interrupt_value: dict[str, Any],
    ) -> list[PendingAction]:
        action_requests = interrupt_value.get("action_requests", [])
        review_configs = {
            config["action_name"]: config
            for config in interrupt_value.get("review_configs", [])
            if isinstance(config, dict) and "action_name" in config
        }
        pending_actions: list[PendingAction] = []
        for action in action_requests:
            name = action["name"]
            review_config = review_configs.get(name, {})
            allowed_decisions = [
                decision
                for decision in review_config.get("allowed_decisions", [])
                if decision in {"approve", "reject"}
            ]
            pending_actions.append(
                PendingAction(
                    name=name,
                    args=action["args"],
                    description=action["description"],
                    allowed_decisions=allowed_decisions,
                )
            )
        if not pending_actions:
            raise AgentRuntimeError("Agent返回了空的审批请求")
        return pending_actions
