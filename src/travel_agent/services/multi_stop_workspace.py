"""Local multi-stop drafts and explicitly confirmed, immutable saved snapshots."""

import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from travel_agent.config import get_api_settings
from travel_agent.domain.multi_stop import (
    MultiStopDraft,
    MultiStopPlanningResult,
    SavedMultiStopJourney,
)
from travel_agent.services.multi_stop_selection import select_multi_stop_plans


class MultiStopWorkspaceError(RuntimeError):
    pass


class MultiStopDraftNotFound(MultiStopWorkspaceError):
    pass


class MultiStopWorkspace:
    """Shared local storage, not account isolation; existing v1 JSONL is untouched."""

    def __init__(self, path: Path | None = None):
        self.path = path or get_api_settings().multi_stop_storage_path

    @contextmanager
    def _database(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            database = sqlite3.connect(self.path, timeout=10)
        except (OSError, sqlite3.Error) as exc:
            raise MultiStopWorkspaceError("多站点行程存储暂时不可用") from exc
        try:
            database.execute("""CREATE TABLE IF NOT EXISTS multi_stop_drafts
                              (id TEXT PRIMARY KEY, payload TEXT NOT NULL)""")
            database.execute("""CREATE TABLE IF NOT EXISTS saved_multi_stop
                              (id TEXT PRIMARY KEY, draft_id TEXT NOT NULL,
                               plan_id TEXT NOT NULL, payload TEXT NOT NULL,
                               saved_at TEXT NOT NULL, UNIQUE(draft_id, plan_id))""")
            yield database
            database.commit()
        except sqlite3.Error as exc:
            database.rollback()
            raise MultiStopWorkspaceError("多站点行程存储暂时不可用") from exc
        finally:
            database.close()

    async def create_draft(self, result: MultiStopPlanningResult) -> MultiStopDraft:
        draft = MultiStopDraft(draft_id=uuid4(), result=result, created_at=datetime.now(UTC))
        await asyncio.to_thread(self._write_draft, draft.draft_id, draft.model_dump_json())
        return draft

    def _write_draft(self, identity, payload):
        with self._database() as database:
            database.execute(
                "INSERT INTO multi_stop_drafts VALUES (?, ?)", (str(identity), payload)
            )

    async def get_draft(self, draft_id: UUID) -> MultiStopDraft:
        return await asyncio.to_thread(self._get_draft, draft_id)

    async def get_plan_draft(self, draft_id: UUID, plan_id: str) -> MultiStopDraft:
        """Resolve an exact current or immutable saved plan for read-only checks."""
        return await asyncio.to_thread(self._get_plan_draft, draft_id, plan_id)

    def _get_plan_draft(self, draft_id, plan_id):
        with self._database() as database:
            row = database.execute(
                "SELECT payload FROM multi_stop_drafts WHERE id=?", (str(draft_id),),
            ).fetchone()
            if row:
                draft = MultiStopDraft.model_validate_json(row[0])
                if any(plan.plan_id == plan_id for plan in draft.result.selection.plans):
                    return draft
            # A later manual choice may remove an old plan from the displayed
            # draft. A saved snapshot still owns the original full timeline.
            saved = database.execute(
                "SELECT payload FROM saved_multi_stop WHERE draft_id=? AND plan_id=?",
                (str(draft_id), plan_id),
            ).fetchone()
            if saved:
                return SavedMultiStopJourney.model_validate_json(saved[0]).draft
            if row is None:
                raise MultiStopDraftNotFound("多站点草案不存在，请重新规划")
            raise ValueError("所选方案不属于当前草案或已保存快照")

    def _get_draft(self, draft_id):
        with self._database() as database:
            row = database.execute(
                "SELECT payload FROM multi_stop_drafts WHERE id=?", (str(draft_id),)
            ).fetchone()
            if row is None:
                raise MultiStopDraftNotFound("多站点草案不存在，请重新规划")
            return MultiStopDraft.model_validate_json(row[0])

    async def save(self, draft_id: UUID, selected_plan_id: str) -> SavedMultiStopJourney:
        return await asyncio.to_thread(self._save, draft_id, selected_plan_id)

    async def select(self, draft_id: UUID, candidate_ids: list[str]) -> MultiStopDraft:
        original = await self.get_draft(draft_id)
        selection = await asyncio.to_thread(
            select_multi_stop_plans,
            original.result.request,
            original.result.segments,
            selected_candidate_ids=candidate_ids,
        )
        selection.limitations.extend(
            [
                "本次分段选择复用已有查询快照，未重新请求路线。",
                "路线仍不能保证未来路况、班次或准点。",
            ]
        )
        result = original.result.model_copy(
            update={"selection": selection, "route_service_calls": 0}
        )
        return await self.create_draft(result)

    def _save(self, draft_id, selected_plan_id):
        with self._database() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT payload FROM multi_stop_drafts WHERE id=?", (str(draft_id),)
            ).fetchone()
            if row is None:
                raise MultiStopDraftNotFound("多站点草案不存在，请重新规划")
            draft = MultiStopDraft.model_validate_json(row[0])
            if not any(
                plan.plan_id == selected_plan_id and plan.evaluation.status == "feasible"
                for plan in draft.result.selection.plans
            ):
                raise ValueError("所选方案不属于可信草案中的可行行程")
            existing = database.execute(
                "SELECT payload FROM saved_multi_stop WHERE draft_id=? AND plan_id=?",
                (str(draft_id), selected_plan_id),
            ).fetchone()
            if existing:
                return SavedMultiStopJourney.model_validate_json(existing[0])
            saved = SavedMultiStopJourney(
                itinerary_id=uuid4(),
                draft=draft,
                selected_plan_id=selected_plan_id,
                saved_at=datetime.now(UTC),
            )
            database.execute(
                "INSERT INTO saved_multi_stop VALUES (?, ?, ?, ?, ?)",
                (
                    str(saved.itinerary_id),
                    str(draft_id),
                    selected_plan_id,
                    saved.model_dump_json(),
                    saved.saved_at.isoformat(),
                ),
            )
            return saved

    async def list_saved(self, limit: int = 20) -> list[SavedMultiStopJourney]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return await asyncio.to_thread(self._list_saved, limit)

    def _list_saved(self, limit):
        if not self.path.is_file():
            return []
        with self._database() as database:
            rows = database.execute(
                "SELECT payload FROM saved_multi_stop ORDER BY saved_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [SavedMultiStopJourney.model_validate_json(row[0]) for row in rows]
