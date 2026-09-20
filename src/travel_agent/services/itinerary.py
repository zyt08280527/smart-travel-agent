from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from travel_agent.config import get_settings
from travel_agent.domain.itinerary import ItineraryDraft, SavedItinerary


class ItineraryServiceError(RuntimeError):
    """A user-safe error raised when itinerary persistence fails."""


class ItineraryNotFoundError(ItineraryServiceError):
    """Raised when a requested saved itinerary does not exist."""


class ItineraryService:
    """Persist itinerary records as newline-delimited JSON."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self._storage_path = storage_path or get_settings().itinerary_storage_path

    async def save(self, draft: ItineraryDraft) -> SavedItinerary:
        """Create one saved record and append it without blocking the event loop."""
        record = SavedItinerary(
            **draft.model_dump(),
            itinerary_id=str(uuid4()),
            saved_at=datetime.now(UTC),
        )
        try:
            await asyncio.to_thread(self._append_record, record)
        except OSError as exc:
            raise ItineraryServiceError("行程保存失败，请稍后重试") from exc
        return record

    async def list(self, limit: int = 20) -> list[SavedItinerary]:
        """Return the newest valid saved records without blocking the event loop."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        try:
            records = await asyncio.to_thread(self._read_records)
        except (OSError, UnicodeError) as exc:
            raise ItineraryServiceError("行程读取失败，请稍后重试") from exc
        return sorted(records, key=lambda item: item.saved_at, reverse=True)[:limit]

    async def delete(self, itinerary_id: str) -> None:
        """Delete one saved record using an atomic file replacement."""
        try:
            deleted = await asyncio.to_thread(self._delete_record, itinerary_id)
        except (OSError, UnicodeError) as exc:
            raise ItineraryServiceError("行程删除失败，请稍后重试") from exc
        if not deleted:
            raise ItineraryNotFoundError("已保存行程不存在")

    def _append_record(self, record: SavedItinerary) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self._storage_path.open("a", encoding="utf-8") as file:
            file.write(record.model_dump_json())
            file.write("\n")

    def _read_records(self) -> list[SavedItinerary]:
        if not self._storage_path.exists():
            return []

        records: list[SavedItinerary] = []
        with self._storage_path.open("r", encoding="utf-8") as file:
            for line in file:
                normalized = line.strip()
                if not normalized:
                    continue
                try:
                    records.append(SavedItinerary.model_validate_json(normalized))
                except ValidationError:
                    # A partial/corrupt JSONL line must not hide other valid trips.
                    continue
        return records

    def _delete_record(self, itinerary_id: str) -> bool:
        records = self._read_records()
        remaining = [
            record for record in records if record.itinerary_id != itinerary_id
        ]
        if len(remaining) == len(records):
            return False

        temporary_path = self._storage_path.with_name(
            f".{self._storage_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temporary_path.open("w", encoding="utf-8") as file:
                for record in remaining:
                    file.write(record.model_dump_json())
                    file.write("\n")
            temporary_path.replace(self._storage_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return True
