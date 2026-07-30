import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite

from travel_agent.config import get_settings
from travel_agent.domain.conversation import (
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    MessageRole,
    StoredConversationMessage,
)

Clock = Callable[[], datetime]


class ConversationRepositoryError(RuntimeError):
    """Raised when conversation persistence cannot complete safely."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _conversation_title(first_user_message: str) -> str:
    """Build a deterministic compact title from the first user message."""
    normalized = " ".join(first_user_message.split())
    if not normalized:
        raise ValueError("会话标题来源消息不能为空")
    if len(normalized) <= 40:
        return normalized
    return f"{normalized[:40]}…"


class ConversationRepository:
    """Store a product-facing conversation index and visible messages."""

    def __init__(
        self,
        storage_path: Path | None = None,
        clock: Clock = _utc_now,
    ) -> None:
        self._storage_path = (
            storage_path or get_settings().conversation_storage_path
        )
        self._clock = clock

    async def initialize(self) -> None:
        """Create the conversation tables and indexes when absent."""
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._storage_path) as database:
            await database.execute("PRAGMA journal_mode=WAL")
            await database.execute("PRAGMA foreign_keys=ON")
            await database.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    thread_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('ready', 'approval_required', 'error')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    cards_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES conversations(thread_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
                    ON conversations(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_messages_thread_created
                    ON conversation_messages(thread_id, created_at ASC);
                """
            )
            await database.commit()

    async def upsert_conversation(
        self,
        thread_id: UUID,
        first_user_message: str,
        status: ConversationStatus = "ready",
    ) -> ConversationSummary:
        """Create a conversation or touch it without replacing its title."""
        title = _conversation_title(first_user_message)
        now = self._clock().isoformat()
        try:
            async with aiosqlite.connect(self._storage_path) as database:
                database.row_factory = aiosqlite.Row
                await database.execute("PRAGMA foreign_keys=ON")
                await database.execute(
                    """
                    INSERT INTO conversations (
                        thread_id, title, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (str(thread_id), title, status, now, now),
                )
                await database.commit()
                row = await self._fetch_summary_row(database, thread_id)
        except aiosqlite.Error as exc:
            raise ConversationRepositoryError(
                "会话元数据保存失败"
            ) from exc
        if row is None:
            raise ConversationRepositoryError("会话元数据保存失败")
        return self._summary_from_row(row)

    async def set_status(
        self,
        thread_id: UUID,
        status: ConversationStatus,
    ) -> ConversationSummary:
        """Update one existing conversation status and modification time."""
        now = self._clock().isoformat()
        try:
            async with aiosqlite.connect(self._storage_path) as database:
                database.row_factory = aiosqlite.Row
                cursor = await database.execute(
                    """
                    UPDATE conversations
                    SET status = ?, updated_at = ?
                    WHERE thread_id = ?
                    """,
                    (status, now, str(thread_id)),
                )
                await database.commit()
                if cursor.rowcount == 0:
                    raise ConversationRepositoryError("会话不存在")
                row = await self._fetch_summary_row(database, thread_id)
        except aiosqlite.Error as exc:
            raise ConversationRepositoryError("会话状态更新失败") from exc
        if row is None:
            raise ConversationRepositoryError("会话状态更新失败")
        return self._summary_from_row(row)

    async def append_message(
        self,
        thread_id: UUID,
        role: MessageRole,
        content: str,
        cards: list[dict[str, Any]] | None = None,
    ) -> StoredConversationMessage:
        """Append one user-visible message and touch the conversation."""
        message = StoredConversationMessage(
            message_id=uuid4(),
            thread_id=thread_id,
            role=role,
            content=content,
            cards=cards or [],
            created_at=self._clock(),
        )
        try:
            async with aiosqlite.connect(self._storage_path) as database:
                await database.execute("PRAGMA foreign_keys=ON")
                await database.execute(
                    """
                    INSERT INTO conversation_messages (
                        message_id, thread_id, role, content,
                        cards_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(message.message_id),
                        str(thread_id),
                        role,
                        content,
                        json.dumps(
                            message.cards,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        message.created_at.isoformat(),
                    ),
                )
                await database.execute(
                    """
                    UPDATE conversations
                    SET updated_at = ?
                    WHERE thread_id = ?
                    """,
                    (message.created_at.isoformat(), str(thread_id)),
                )
                await database.commit()
        except aiosqlite.IntegrityError as exc:
            raise ConversationRepositoryError("会话不存在") from exc
        except aiosqlite.Error as exc:
            raise ConversationRepositoryError("会话消息保存失败") from exc
        return message

    async def list_conversations(
        self,
        limit: int = 50,
    ) -> list[ConversationSummary]:
        """Return recently updated conversations in descending order."""
        if not 1 <= limit <= 100:
            raise ValueError("会话列表数量必须在 1 到 100 之间")
        try:
            async with aiosqlite.connect(self._storage_path) as database:
                database.row_factory = aiosqlite.Row
                cursor = await database.execute(
                    """
                    SELECT thread_id, title, status, created_at, updated_at
                    FROM conversations
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise ConversationRepositoryError("会话列表读取失败") from exc
        return [self._summary_from_row(row) for row in rows]

    async def get_conversation(
        self,
        thread_id: UUID,
    ) -> ConversationDetail | None:
        """Return one conversation and its visible messages."""
        try:
            async with aiosqlite.connect(self._storage_path) as database:
                database.row_factory = aiosqlite.Row
                summary_row = await self._fetch_summary_row(
                    database,
                    thread_id,
                )
                if summary_row is None:
                    return None
                cursor = await database.execute(
                    """
                    SELECT message_id, thread_id, role, content,
                           cards_json, created_at
                    FROM conversation_messages
                    WHERE thread_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (str(thread_id),),
                )
                message_rows = await cursor.fetchall()
        except aiosqlite.Error as exc:
            raise ConversationRepositoryError("会话历史读取失败") from exc

        return ConversationDetail(
            conversation=self._summary_from_row(summary_row),
            messages=[
                self._message_from_row(row) for row in message_rows
            ],
        )

    async def delete_conversation(self, thread_id: UUID) -> bool:
        """Delete product history; foreign keys remove its messages."""
        try:
            async with aiosqlite.connect(self._storage_path) as database:
                await database.execute("PRAGMA foreign_keys=ON")
                cursor = await database.execute(
                    "DELETE FROM conversations WHERE thread_id = ?",
                    (str(thread_id),),
                )
                await database.commit()
                return cursor.rowcount > 0
        except aiosqlite.Error as exc:
            raise ConversationRepositoryError("会话删除失败") from exc

    @staticmethod
    async def _fetch_summary_row(
        database: aiosqlite.Connection,
        thread_id: UUID,
    ) -> aiosqlite.Row | None:
        cursor = await database.execute(
            """
            SELECT thread_id, title, status, created_at, updated_at
            FROM conversations
            WHERE thread_id = ?
            """,
            (str(thread_id),),
        )
        return await cursor.fetchone()

    @staticmethod
    def _summary_from_row(row: aiosqlite.Row) -> ConversationSummary:
        return ConversationSummary(
            thread_id=row["thread_id"],
            title=row["title"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _message_from_row(row: aiosqlite.Row) -> StoredConversationMessage:
        return StoredConversationMessage(
            message_id=row["message_id"],
            thread_id=row["thread_id"],
            role=row["role"],
            content=row["content"],
            cards=json.loads(row["cards_json"]),
            created_at=row["created_at"],
        )
