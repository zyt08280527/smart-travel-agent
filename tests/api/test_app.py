import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from travel_agent.api.app import create_app
from travel_agent.api.runtime import (
    ConversationNotFoundError,
    PendingApprovalNotFoundError,
)
from travel_agent.api.schemas import (
    AgentResponse,
    AgentStreamEvent,
    ApprovalRequest,
    PendingAction,
    WeatherResultCard,
)
from travel_agent.domain.conversation import (
    ConversationDetail,
    ConversationSummary,
    StoredConversationMessage,
)
from travel_agent.domain.itinerary import SavedItinerary

THREAD_ID = UUID("12345678-1234-5678-1234-567812345678")
MISSING_THREAD_ID = UUID("87654321-4321-8765-4321-876543218765")


class FakeRuntime:
    """A deterministic runtime used by FastAPI endpoint tests."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.tool_names = ["query_current_weather", "save_itinerary"]

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def chat(
        self,
        message: str,
        thread_id: UUID | None = None,
    ) -> AgentResponse:
        current_thread_id = thread_id or THREAD_ID
        if "保存" in message:
            return AgentResponse(
                status="approval_required",
                thread_id=current_thread_id,
                pending_actions=[
                    PendingAction(
                        name="save_itinerary",
                        args={"title": "测试行程"},
                        description="请确认是否保存以下行程。",
                        allowed_decisions=["approve", "reject"],
                    )
                ],
            )
        return AgentResponse(
            status="completed",
            thread_id=current_thread_id,
            answer="测试回答",
        )

    async def decide(
        self,
        thread_id: UUID,
        request: ApprovalRequest,
    ) -> AgentResponse:
        if thread_id == MISSING_THREAD_ID:
            raise PendingApprovalNotFoundError("该任务没有等待审批的操作")
        return AgentResponse(
            status="completed",
            thread_id=thread_id,
            answer=f"审批结果：{request.decision}",
        )

    async def stream_chat(
        self,
        _message: str,
        thread_id: UUID | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        current_thread_id = thread_id or THREAD_ID
        yield AgentStreamEvent(
            type="run_started",
            thread_id=current_thread_id,
        )
        yield AgentStreamEvent(
            type="assistant_delta",
            thread_id=current_thread_id,
            delta="测试",
        )
        yield AgentStreamEvent(
            type="result_card",
            thread_id=current_thread_id,
            card=WeatherResultCard(
                type="weather",
                city="深圳",
                country="中国",
                admin1="广东",
                temperature_c=25.1,
                apparent_temperature_c=29.5,
                precipitation_mm=0.0,
                wind_speed_kmh=10.6,
                condition="阴天",
                observed_at="2026-07-28T21:45",
            ),
        )
        yield AgentStreamEvent(
            type="final",
            thread_id=current_thread_id,
            answer="测试回答",
        )
        yield AgentStreamEvent(type="done", thread_id=current_thread_id)

    async def list_conversations(
        self,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        assert limit == 50
        return [
            ConversationSummary(
                thread_id=THREAD_ID,
                title="深圳天气",
                status="ready",
                created_at=datetime(2026, 7, 28, tzinfo=UTC),
                updated_at=datetime(2026, 7, 28, 1, tzinfo=UTC),
            )
        ]

    async def get_conversation(self, thread_id: UUID) -> ConversationDetail:
        if thread_id == MISSING_THREAD_ID:
            raise ConversationNotFoundError("会话不存在")
        summary = (await self.list_conversations())[0]
        return ConversationDetail(
            conversation=summary,
            messages=[
                StoredConversationMessage(
                    message_id=UUID(
                        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
                    ),
                    thread_id=thread_id,
                    role="user",
                    content="深圳天气",
                    created_at=datetime(2026, 7, 28, tzinfo=UTC),
                )
            ],
        )

    async def delete_conversation(self, thread_id: UUID) -> None:
        if thread_id == MISSING_THREAD_ID:
            raise ConversationNotFoundError("会话不存在")


class FakeItineraryService:
    deleted_ids: list[str] = []

    async def list(self, limit: int = 20) -> list[SavedItinerary]:
        assert limit == 5
        return [
            SavedItinerary(
                title="深圳通勤",
                origin="深圳大学粤海校区",
                destination="深圳大学丽湖校区",
                travel_mode="transit",
                distance_m=22007,
                duration_s=5717,
                itinerary_id="saved-itinerary",
                saved_at=datetime(2026, 9, 20, tzinfo=UTC),
            )
        ]

    async def delete(self, itinerary_id: str) -> None:
        self.deleted_ids.append(itinerary_id)


def test_health_uses_lifespan_managed_runtime() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/health")
        assert runtime.started is True
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "tools": ["query_current_weather", "save_itinerary"],
        }

    assert runtime.stopped is True


def test_cors_allows_react_development_origin() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.options(
            "/api/chat",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "Content-Type" in response.headers["access-control-allow-headers"]


def test_chat_returns_completed_or_pending_response() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        completed = client.post(
            "/api/chat",
            json={"message": "你好", "thread_id": str(THREAD_ID)},
        )
        pending = client.post(
            "/api/chat",
            json={"message": "请保存行程", "thread_id": str(THREAD_ID)},
        )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["answer"] == "测试回答"
    assert pending.status_code == 200
    assert pending.json()["status"] == "approval_required"
    assert pending.json()["pending_actions"][0]["name"] == "save_itinerary"


def test_chat_stream_returns_one_json_event_per_line() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={"message": "你好", "thread_id": str(THREAD_ID)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/x-ndjson"
    )
    events = [
        AgentStreamEvent.model_validate_json(line)
        for line in response.text.splitlines()
    ]
    assert [event.type for event in events] == [
        "run_started",
        "assistant_delta",
        "result_card",
        "final",
        "done",
    ]
    assert events[1].delta == "测试"
    assert events[2].card is not None
    assert events[2].card.type == "weather"
    assert events[3].answer == "测试回答"

    raw_events = [json.loads(line) for line in response.text.splitlines()]
    assert raw_events[2]["card"]["type"] == "weather"


def test_approval_endpoint_resumes_thread() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/threads/{THREAD_ID}/approval",
            json={"decision": "approve"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["answer"] == "审批结果：approve"


def test_approval_endpoint_returns_404_for_unknown_thread() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.post(
            f"/api/threads/{MISSING_THREAD_ID}/approval",
            json={"decision": "reject"},
        )

    assert response.status_code == 404
    assert "没有等待审批" in response.json()["detail"]


def test_conversation_list_and_detail_endpoints() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        conversations = client.get("/api/conversations")
        detail = client.get(f"/api/conversations/{THREAD_ID}")

    assert conversations.status_code == 200
    assert conversations.json()[0]["title"] == "深圳天气"
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["content"] == "深圳天气"


def test_itinerary_list_endpoint_returns_saved_records() -> None:
    runtime = FakeRuntime()
    app = create_app(
        runtime_factory=lambda: runtime,  # type: ignore[arg-type]
        itinerary_service_factory=FakeItineraryService,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.get("/api/itineraries?limit=5")

    assert response.status_code == 200
    assert response.json()[0]["itinerary_id"] == "saved-itinerary"
    assert response.json()[0]["travel_mode"] == "transit"


def test_itinerary_list_endpoint_validates_limit() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get("/api/itineraries?limit=0")

    assert response.status_code == 422


def test_itinerary_delete_endpoint_removes_saved_record() -> None:
    runtime = FakeRuntime()
    FakeItineraryService.deleted_ids = []
    app = create_app(
        runtime_factory=lambda: runtime,  # type: ignore[arg-type]
        itinerary_service_factory=FakeItineraryService,  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        response = client.delete("/api/itineraries/saved-itinerary")

    assert response.status_code == 204
    assert FakeItineraryService.deleted_ids == ["saved-itinerary"]


def test_conversation_detail_returns_404_when_missing() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        response = client.get(f"/api/conversations/{MISSING_THREAD_ID}")

    assert response.status_code == 404
    assert response.json()["detail"] == "会话不存在"


def test_conversation_delete_returns_204_or_404() -> None:
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)  # type: ignore[arg-type]

    with TestClient(app) as client:
        deleted = client.delete(f"/api/conversations/{THREAD_ID}")
        missing = client.delete(f"/api/conversations/{MISSING_THREAD_ID}")

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert missing.status_code == 404
    assert missing.json()["detail"] == "会话不存在"
