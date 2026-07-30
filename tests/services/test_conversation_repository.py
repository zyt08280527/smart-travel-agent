from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite
import pytest

from travel_agent.services.conversation import (
    ConversationRepository,
    ConversationRepositoryError,
)

THREAD_ID = UUID("12345678-1234-5678-1234-567812345678")


class SequenceClock:
    """Return deterministic timestamps in test-defined order."""

    def __init__(self, *values: datetime) -> None:
        self._values = iter(values)

    def __call__(self) -> datetime:
        return next(self._values)


@pytest.mark.asyncio
async def test_upsert_preserves_first_title_and_lists_recent_first(
    tmp_path: Path,
) -> None:
    first_time = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    second_time = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    repository = ConversationRepository(
        tmp_path / "conversations.sqlite",
        clock=SequenceClock(first_time, second_time),
    )
    await repository.initialize()

    created = await repository.upsert_conversation(
        THREAD_ID,
        "  深圳现在天气怎么样？\n请根据实时数据回答。  ",
    )
    updated = await repository.upsert_conversation(
        THREAD_ID,
        "这条后续消息不能覆盖标题",
        status="approval_required",
    )
    conversations = await repository.list_conversations()

    assert created.title == "深圳现在天气怎么样？ 请根据实时数据回答。"
    assert updated.title == created.title
    assert updated.created_at == first_time
    assert updated.updated_at == second_time
    assert updated.status == "approval_required"
    assert conversations == [updated]


@pytest.mark.asyncio
async def test_append_and_read_visible_messages_with_cards(
    tmp_path: Path,
) -> None:
    repository = ConversationRepository(
        tmp_path / "conversations.sqlite",
        clock=SequenceClock(
            datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        ),
    )
    await repository.initialize()
    await repository.upsert_conversation(THREAD_ID, "深圳天气")

    user_message = await repository.append_message(
        THREAD_ID,
        "user",
        "深圳天气怎么样？",
    )
    assistant_message = await repository.append_message(
        THREAD_ID,
        "assistant",
        "深圳当前天气为阴天。",
        cards=[{"type": "weather", "city": "深圳"}],
    )
    detail = await repository.get_conversation(THREAD_ID)

    assert detail is not None
    assert detail.messages == [user_message, assistant_message]
    assert detail.messages[1].cards == [
        {"type": "weather", "city": "深圳"}
    ]
    assert detail.conversation.updated_at == assistant_message.created_at


@pytest.mark.asyncio
async def test_delete_cascades_to_messages(tmp_path: Path) -> None:
    storage_path = tmp_path / "conversations.sqlite"
    repository = ConversationRepository(storage_path)
    await repository.initialize()
    await repository.upsert_conversation(THREAD_ID, "待删除会话")
    await repository.append_message(THREAD_ID, "user", "测试消息")

    assert await repository.delete_conversation(THREAD_ID) is True
    assert await repository.get_conversation(THREAD_ID) is None
    assert await repository.delete_conversation(THREAD_ID) is False

    async with aiosqlite.connect(storage_path) as database:
        cursor = await database.execute(
            "SELECT COUNT(*) FROM conversation_messages"
        )
        row = await cursor.fetchone()
    assert row == (0,)


@pytest.mark.asyncio
async def test_append_requires_existing_conversation(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.sqlite")
    await repository.initialize()

    with pytest.raises(ConversationRepositoryError, match="会话不存在"):
        await repository.append_message(THREAD_ID, "user", "测试消息")


@pytest.mark.asyncio
async def test_list_rejects_invalid_limit(tmp_path: Path) -> None:
    repository = ConversationRepository(tmp_path / "conversations.sqlite")
    await repository.initialize()

    with pytest.raises(ValueError, match="1 到 100"):
        await repository.list_conversations(limit=0)
