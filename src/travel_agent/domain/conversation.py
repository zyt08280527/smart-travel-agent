from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ConversationStatus = Literal["ready", "approval_required", "error"]
MessageRole = Literal["user", "assistant"]


class ConversationSummary(BaseModel):
    """User-facing metadata for one Agent conversation."""

    thread_id: UUID
    title: str = Field(min_length=1, max_length=41)
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime


class StoredConversationMessage(BaseModel):
    """One user-visible message stored independently of checkpoints."""

    message_id: UUID
    thread_id: UUID
    role: MessageRole
    content: str = Field(min_length=1)
    cards: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime


class ConversationDetail(BaseModel):
    """Conversation metadata together with its user-visible messages."""

    conversation: ConversationSummary
    messages: list[StoredConversationMessage]
