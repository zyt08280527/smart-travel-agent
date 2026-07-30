import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from travel_agent.config import get_settings
from travel_agent.domain.itinerary import ItineraryDraft, SavedItinerary


class ItineraryServiceError(RuntimeError):
    """A user-safe error raised when an itinerary cannot be saved."""


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

    def _append_record(self, record: SavedItinerary) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        with self._storage_path.open("a", encoding="utf-8") as file:
            file.write(record.model_dump_json())
            file.write("\n")
