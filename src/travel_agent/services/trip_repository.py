"""Owner-scoped SQLite business storage with append-only route revisions."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from travel_agent.domain.trip import TripContent, TripDetail, TripOwner, TripRevision, TripSummary

APPLICATION_ID = 0x54525032
DATABASE_VERSION = 1


class TripStorageError(RuntimeError):
    """Storage is unavailable or has an unsupported schema."""


class TripNotFoundError(TripStorageError):
    """Missing and foreign-owned trips deliberately share one result."""


class TripRevisionConflict(TripStorageError):
    """The user edited a revision that is no longer current."""


class TripRepository:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path

    @contextmanager
    def _database(
        self, *, write: bool = False, initialize: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        if not initialize and not self.storage_path.is_file():
            raise TripStorageError("行程数据库尚未初始化")
        try:
            connection = sqlite3.connect(self.storage_path, timeout=10, isolation_level=None)
        except sqlite3.Error as exc:
            raise TripStorageError("行程数据库无法打开") from exc
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if not initialize:
                self._check_schema(connection)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise TripStorageError("行程数据库操作失败") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _check_schema(database: sqlite3.Connection) -> None:
        app_id = database.execute("PRAGMA application_id").fetchone()[0]
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if app_id != APPLICATION_ID or version != DATABASE_VERSION:
            raise TripStorageError("行程数据库标识或版本不受支持，请先检查迁移")

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self._database(write=True, initialize=True) as database:
            app_id = database.execute("PRAGMA application_id").fetchone()[0]
            version = database.execute("PRAGMA user_version").fetchone()[0]
            tables = database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if app_id == APPLICATION_ID and version == DATABASE_VERSION:
                return
            if app_id or version or tables:
                raise TripStorageError("目标不是空库或受支持的行程库，未进行迁移")
            statements = [
                """CREATE TABLE trip_owners (
                    owner_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL)""",
                """CREATE TABLE trips (
                    trip_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL REFERENCES trip_owners(owner_id),
                    schema_version INTEGER NOT NULL CHECK(schema_version=2),
                    title TEXT NOT NULL, timezone TEXT NOT NULL,
                    current_revision INTEGER NOT NULL CHECK(current_revision>=1),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                """CREATE TABLE trip_revisions (
                    trip_id TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL CHECK(revision>=1),
                    reason TEXT NOT NULL, content_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(trip_id, revision))""",
                """CREATE TABLE legacy_trip_imports (
                    legacy_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL REFERENCES trip_owners(owner_id),
                    trip_id TEXT NOT NULL REFERENCES trips(trip_id),
                    source_sha256 TEXT NOT NULL, record_sha256 TEXT NOT NULL)""",
                """CREATE TABLE trip_schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""",
                "CREATE INDEX idx_trips_owner_updated ON trips(owner_id, updated_at DESC)",
                """CREATE TRIGGER revisions_no_update BEFORE UPDATE ON trip_revisions
                    BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END""",
                """CREATE TRIGGER revisions_no_delete BEFORE DELETE ON trip_revisions
                    BEGIN SELECT RAISE(ABORT, 'revisions are immutable'); END""",
            ]
            for statement in statements:
                database.execute(statement)
            database.execute(
                "INSERT INTO trip_schema_migrations VALUES (?, ?)",
                (DATABASE_VERSION, datetime.now(UTC).isoformat()),
            )
            database.execute(f"PRAGMA application_id={APPLICATION_ID}")
            database.execute(f"PRAGMA user_version={DATABASE_VERSION}")

    async def create_owner(self, display_name: str, owner_id: UUID | None = None) -> TripOwner:
        owner = TripOwner(owner_id=owner_id or uuid4(), display_name=display_name,
                          created_at=datetime.now(UTC))
        return await asyncio.to_thread(self._create_owner, owner)

    def _create_owner(self, owner: TripOwner) -> TripOwner:
        with self._database(write=True) as database:
            database.execute(
                "INSERT INTO trip_owners VALUES (?, ?, ?) ON CONFLICT(owner_id) DO NOTHING",
                (str(owner.owner_id), owner.display_name, owner.created_at.isoformat()),
            )
            row = database.execute(
                "SELECT * FROM trip_owners WHERE owner_id=?", (str(owner.owner_id),),
            ).fetchone()
            return TripOwner.model_validate(dict(row))

    @staticmethod
    def _owned_trip(
        database: sqlite3.Connection, owner_id: UUID, trip_id: UUID,
    ) -> sqlite3.Row:
        row = database.execute(
            "SELECT * FROM trips WHERE trip_id=? AND owner_id=?", (str(trip_id), str(owner_id)),
        ).fetchone()
        if row is None:
            raise TripNotFoundError("行程不存在")
        return row

    @staticmethod
    def _insert_trip(
        database: sqlite3.Connection, owner_id: UUID, content: TripContent, reason: str,
    ) -> UUID:
        if database.execute("SELECT 1 FROM trip_owners WHERE owner_id=?",
                            (str(owner_id),)).fetchone() is None:
            raise TripNotFoundError("行程归属用户不存在")
        trip_id = uuid4()
        now = datetime.now(UTC).isoformat()
        database.execute(
            "INSERT INTO trips VALUES (?, ?, 2, ?, ?, 1, ?, ?)",
            (str(trip_id), str(owner_id), content.title, content.timezone, now, now),
        )
        database.execute(
            "INSERT INTO trip_revisions VALUES (?, 1, ?, ?, ?)",
            (str(trip_id), reason, content.model_dump_json(), now),
        )
        return trip_id

    async def create(self, owner_id: UUID, content: TripContent) -> TripDetail:
        # Freeze caller-owned mutable Pydantic objects before dispatching to a thread.
        content = TripContent.model_validate_json(content.model_dump_json())
        return await asyncio.to_thread(self._create, owner_id, content)

    def _create(self, owner_id: UUID, content: TripContent) -> TripDetail:
        with self._database(write=True) as database:
            trip_id = self._insert_trip(database, owner_id, content, "created")
            return self._detail(database, owner_id, trip_id)

    @staticmethod
    def _revision(row: sqlite3.Row) -> TripRevision:
        return TripRevision(trip_id=row["trip_id"], revision=row["revision"], reason=row["reason"],
                            content=TripContent.model_validate_json(row["content_json"]),
                            created_at=row["created_at"])

    def _detail(
        self, database: sqlite3.Connection, owner_id: UUID, trip_id: UUID,
    ) -> TripDetail:
        trip = self._owned_trip(database, owner_id, trip_id)
        revision = database.execute(
            "SELECT * FROM trip_revisions WHERE trip_id=? AND revision=?",
            (str(trip_id), trip["current_revision"]),
        ).fetchone()
        return TripDetail(trip=TripSummary.model_validate(dict(trip)),
                          current=self._revision(revision))

    async def get(self, owner_id: UUID, trip_id: UUID) -> TripDetail:
        return await asyncio.to_thread(self._get, owner_id, trip_id)

    def _get(self, owner_id: UUID, trip_id: UUID) -> TripDetail:
        with self._database() as database:
            return self._detail(database, owner_id, trip_id)

    async def list(self, owner_id: UUID, limit: int = 20) -> list[TripSummary]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return await asyncio.to_thread(self._list, owner_id, limit)

    def _list(self, owner_id: UUID, limit: int) -> list[TripSummary]:
        with self._database() as database:
            rows = database.execute(
                "SELECT * FROM trips WHERE owner_id=? ORDER BY updated_at DESC, trip_id LIMIT ?",
                (str(owner_id), limit),
            ).fetchall()
            return [TripSummary.model_validate(dict(row)) for row in rows]

    async def revisions(self, owner_id: UUID, trip_id: UUID) -> list[TripRevision]:
        return await asyncio.to_thread(self._revisions, owner_id, trip_id)

    def _revisions(self, owner_id: UUID, trip_id: UUID) -> list[TripRevision]:
        with self._database() as database:
            self._owned_trip(database, owner_id, trip_id)
            rows = database.execute(
                "SELECT * FROM trip_revisions WHERE trip_id=? ORDER BY revision DESC",
                (str(trip_id),),
            ).fetchall()
            return [self._revision(row) for row in rows]

    async def revise(self, owner_id: UUID, trip_id: UUID, content: TripContent,
                     *, expected_revision: int) -> TripDetail:
        content = TripContent.model_validate_json(content.model_dump_json())
        return await asyncio.to_thread(self._revise, owner_id, trip_id, content,
                                       expected_revision, None)

    async def adopt(self, owner_id: UUID, trip_id: UUID, revision: int,
                    *, expected_revision: int) -> TripDetail:
        return await asyncio.to_thread(self._revise, owner_id, trip_id, None,
                                       expected_revision, revision)

    def _revise(
        self, owner_id: UUID, trip_id: UUID, content: TripContent | None,
        expected_revision: int, adopt_revision: int | None,
    ) -> TripDetail:
        with self._database(write=True) as database:
            trip = self._owned_trip(database, owner_id, trip_id)
            if trip["current_revision"] != expected_revision:
                raise TripRevisionConflict("行程已被更新，请重新读取后再修改")
            if adopt_revision is not None:
                row = database.execute(
                    "SELECT * FROM trip_revisions WHERE trip_id=? AND revision=?",
                    (str(trip_id), adopt_revision),
                ).fetchone()
                if row is None:
                    raise TripNotFoundError("历史行程版本不存在")
                content = TripContent.model_validate_json(row["content_json"])
            revision = expected_revision + 1
            if content is None:
                raise ValueError("新版本内容不能为空")
            now = datetime.now(UTC).isoformat()
            database.execute(
                "INSERT INTO trip_revisions VALUES (?, ?, ?, ?, ?)",
                (str(trip_id), revision, "adopted" if adopt_revision is not None else "edited",
                 content.model_dump_json(), now),
            )
            database.execute(
                """UPDATE trips SET current_revision=?, title=?, timezone=?, updated_at=?
                    WHERE trip_id=? AND owner_id=? AND current_revision=?""",
                (revision, content.title, content.timezone, now, str(trip_id),
                 str(owner_id), expected_revision),
            )
            return self._detail(database, owner_id, trip_id)
