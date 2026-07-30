import json
from pathlib import Path

import pytest

from travel_agent.domain.itinerary import ItineraryDraft, SavedItinerary
from travel_agent.services.itinerary import ItineraryService


@pytest.mark.asyncio
async def test_save_appends_valid_json_line(tmp_path: Path) -> None:
    storage_path = tmp_path / "itineraries.jsonl"
    draft = ItineraryDraft(
        title="深大两校区驾车行程",
        origin="深圳大学粤海校区",
        destination="深圳大学丽湖校区",
        travel_mode="driving",
        distance_m=15842.9,
        duration_s=1074.5,
        notes="静态预计时长，不含实时路况",
    )

    saved = await ItineraryService(storage_path).save(draft)

    lines = storage_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    persisted = SavedItinerary.model_validate(json.loads(lines[0]))
    assert persisted == saved
    assert persisted.itinerary_id
    assert persisted.saved_at.tzinfo is not None
    assert persisted.duration_basis == "static_without_live_traffic"
