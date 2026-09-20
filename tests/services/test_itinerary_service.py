import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from travel_agent.domain.itinerary import ItineraryDraft, SavedItinerary
from travel_agent.services.itinerary import ItineraryNotFoundError, ItineraryService


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


@pytest.mark.asyncio
async def test_list_returns_newest_valid_records_and_respects_limit(
    tmp_path: Path,
) -> None:
    storage_path = tmp_path / "itineraries.jsonl"
    older = SavedItinerary(
        title="较早行程",
        origin="A",
        destination="B",
        travel_mode="walking",
        distance_m=100,
        duration_s=60,
        itinerary_id="older",
        saved_at=datetime(2026, 9, 18, tzinfo=UTC),
    )
    newer = SavedItinerary(
        title="较新行程",
        origin="C",
        destination="D",
        travel_mode="transit",
        distance_m=200,
        duration_s=120,
        itinerary_id="newer",
        saved_at=datetime(2026, 9, 19, tzinfo=UTC),
    )
    storage_path.write_text(
        "\n".join(
            [
                older.model_dump_json(),
                "not valid json",
                newer.model_dump_json(),
            ]
        ),
        encoding="utf-8",
    )

    records = await ItineraryService(storage_path).list(limit=1)

    assert [record.itinerary_id for record in records] == ["newer"]


@pytest.mark.asyncio
async def test_list_returns_empty_when_storage_does_not_exist(
    tmp_path: Path,
) -> None:
    records = await ItineraryService(tmp_path / "missing.jsonl").list()

    assert records == []


@pytest.mark.asyncio
async def test_delete_removes_only_requested_record(tmp_path: Path) -> None:
    service = ItineraryService(tmp_path / "itineraries.jsonl")
    first = await service.save(
        ItineraryDraft(
            title="删除目标",
            origin="A",
            destination="B",
            travel_mode="walking",
            distance_m=100,
            duration_s=60,
        )
    )
    second = await service.save(
        ItineraryDraft(
            title="保留目标",
            origin="C",
            destination="D",
            travel_mode="driving",
            distance_m=200,
            duration_s=120,
        )
    )

    await service.delete(first.itinerary_id)

    records = await service.list()
    assert [record.itinerary_id for record in records] == [second.itinerary_id]


@pytest.mark.asyncio
async def test_delete_rejects_unknown_itinerary(tmp_path: Path) -> None:
    service = ItineraryService(tmp_path / "itineraries.jsonl")

    with pytest.raises(ItineraryNotFoundError, match="不存在"):
        await service.delete("missing")
