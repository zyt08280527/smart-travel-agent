from uuid import UUID

import pytest
from pydantic import ValidationError

from travel_agent.api.schemas import (
    AgentResponse,
    ApprovalRequest,
    ChatRequest,
    PendingAction,
)


def test_chat_request_trims_message_and_accepts_thread_id() -> None:
    thread_id = UUID("12345678-1234-5678-1234-567812345678")

    request = ChatRequest(
        message="  深圳现在天气怎么样？  ",
        thread_id=thread_id,
    )

    assert request.message == "深圳现在天气怎么样？"
    assert request.thread_id == thread_id


def test_chat_request_rejects_whitespace_only_message() -> None:
    with pytest.raises(ValidationError, match="消息不能为空"):
        ChatRequest(message="   ")


def test_approval_request_only_accepts_supported_decisions() -> None:
    assert ApprovalRequest(decision="approve").decision == "approve"
    assert ApprovalRequest(decision="reject", message="暂不保存").message == (
        "暂不保存"
    )

    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate({"decision": "skip"})


def test_agent_response_represents_pending_approval() -> None:
    response = AgentResponse(
        status="approval_required",
        thread_id=UUID("12345678-1234-5678-1234-567812345678"),
        pending_actions=[
            PendingAction(
                name="save_itinerary",
                args={"title": "测试行程"},
                description="请确认是否保存以下行程。",
                allowed_decisions=["approve", "reject"],
            )
        ],
    )

    assert response.answer is None
    assert response.pending_actions[0].name == "save_itinerary"
