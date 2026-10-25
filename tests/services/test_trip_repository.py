import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.middleware.test_preference_replanning import direct_transit_payload
from tests.services.test_travel_replanning import build_payload
from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.domain.trip import (
    LegacyTripSnapshot,
    PlanningTripSnapshot,
    TransitTripSnapshot,
    TripContent,
)
from travel_agent.services.legacy_trip_migration import LegacyMigrationError, import_legacy_jsonl
from travel_agent.services.trip_repository import (
    TripNotFoundError,
    TripRepository,
    TripRevisionConflict,
    TripStorageError,
)


def legacy_record(identity="original"):
    return SavedItinerary(
        itinerary_id=identity, title="旧行程", origin="起点", destination="终点",
        travel_mode="walking", distance_m=500, duration_s=600,
        saved_at=datetime(2026, 10, 22, tzinfo=UTC),
    )


def content(title="当前路线"):
    return TripContent(title=title, snapshot=PlanningTripSnapshot(result=build_payload()))


@pytest.fixture
async def repository(tmp_path):
    repository = TripRepository(tmp_path / "trips.sqlite")
    await repository.initialize()
    return repository


async def test_reopen_preserves_complete_snapshot_and_schema(repository):
    owner = await repository.create_owner("用户甲")
    first = await repository.create(owner.owner_id, content())
    await repository.initialize()
    restarted = TripRepository(repository.storage_path)
    loaded = await restarted.get(owner.owner_id, first.trip.trip_id)
    assert loaded == first
    assert loaded.trip.schema_version == 2
    assert loaded.trip.current_revision == 1
    assert loaded.current.content.snapshot.result == content().snapshot.result
    with sqlite3.connect(repository.storage_path) as database:
        assert database.execute("SELECT COUNT(*) FROM trip_schema_migrations").fetchone()[0] == 1


async def test_every_read_and_write_enforces_owner_before_revision_check(repository):
    alice = await repository.create_owner("甲")
    bob = await repository.create_owner("乙")
    trip = (await repository.create(alice.owner_id, content())).trip.trip_id
    assert len(await repository.list(alice.owner_id)) == 1
    assert await repository.list(bob.owner_id) == []
    for action in (
        repository.get(bob.owner_id, trip),
        repository.revisions(bob.owner_id, trip),
        repository.revise(bob.owner_id, trip, content(), expected_revision=99),
        repository.adopt(bob.owner_id, trip, 1, expected_revision=99),
    ):
        with pytest.raises(TripNotFoundError, match="行程不存在"):
            await action
    assert (await repository.get(alice.owner_id, trip)).trip.current_revision == 1


async def test_adopt_creates_new_revision_and_never_mutates_old_versions(repository):
    owner = (await repository.create_owner("甲")).owner_id
    first = await repository.create(owner, content("原方案"))
    trip = first.trip.trip_id
    await repository.revise(owner, trip, content("修改方案"), expected_revision=1)
    before = await repository.revisions(owner, trip)
    adopted = await repository.adopt(owner, trip, 1, expected_revision=2)
    assert adopted.trip.current_revision == 3
    assert adopted.current.reason == "adopted"
    assert adopted.current.content == first.current.content
    assert (await repository.revisions(owner, trip))[1:] == before
    assert adopted.trip.title == "原方案"
    with pytest.raises(TripNotFoundError):
        await repository.adopt(owner, trip, 999, expected_revision=3)
    assert (await repository.get(owner, trip)).trip.current_revision == 3


async def test_concurrent_edits_commit_only_one_winner(repository):
    owner = (await repository.create_owner("甲")).owner_id
    trip = (await repository.create(owner, content())).trip.trip_id
    other_process = TripRepository(repository.storage_path)
    results = await asyncio.gather(
        repository.revise(owner, trip, content("第一次修改"), expected_revision=1),
        other_process.revise(owner, trip, content("第二次修改"), expected_revision=1),
        return_exceptions=True,
    )
    assert sum(isinstance(item, TripRevisionConflict) for item in results) == 1
    assert len(await repository.revisions(owner, trip)) == 2


async def test_revisions_are_immutable_even_for_accidental_sql_updates(repository):
    owner = (await repository.create_owner("甲")).owner_id
    await repository.create(owner, content())
    with sqlite3.connect(repository.storage_path) as database:
        for sql in ("UPDATE trip_revisions SET reason='edited'", "DELETE FROM trip_revisions"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                database.execute(sql)


async def test_unregistered_owner_cannot_create_trip(repository):
    with pytest.raises(TripNotFoundError):
        await repository.create(uuid4(), content())


@pytest.mark.parametrize("foreign", [False, True])
async def test_schema_guard_refuses_future_or_unrelated_database(tmp_path, foreign):
    path = tmp_path / "trips.sqlite"
    if foreign:
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE unrelated(value TEXT)")
    else:
        await TripRepository(path).initialize()
        with sqlite3.connect(path) as database:
            database.execute("PRAGMA user_version=999")
    with sqlite3.connect(path) as database:
        before = database.execute("SELECT * FROM sqlite_master").fetchall()
    with pytest.raises(TripStorageError):
        await TripRepository(path).initialize()
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT * FROM sqlite_master").fetchall() == before
        if not foreign:
            assert database.execute("PRAGMA user_version").fetchone()[0] == 999


async def test_import_dry_run_reports_bad_lines_without_creating_db_or_backup(tmp_path):
    source = tmp_path / "itineraries.jsonl"
    source.write_text(legacy_record().model_dump_json() + "\ninvalid\n", encoding="utf-8")
    repository = TripRepository(tmp_path / "absent.sqlite")
    report = await import_legacy_jsonl(repository, source, uuid4())
    assert report.valid_records == 1
    assert report.invalid_lines == [2]
    assert not repository.storage_path.exists()
    assert list(tmp_path.glob("*.bak")) == []
    with pytest.raises(LegacyMigrationError):
        await import_legacy_jsonl(repository, source, uuid4(), apply=True)
    assert not repository.storage_path.exists()


async def test_import_preserves_source_backup_and_is_repeatable(repository, tmp_path):
    source = tmp_path / "old.jsonl"
    raw = (legacy_record().model_dump_json() + "\n").encode()
    source.write_bytes(raw)
    owner = (await repository.create_owner("旧数据归属" )).owner_id
    report = await import_legacy_jsonl(repository, source, owner, apply=True)
    assert report.created == 1
    assert source.read_bytes() == raw
    backup = Path(report.backup_path)
    assert await asyncio.to_thread(backup.read_bytes) == raw
    trips = await repository.list(owner)
    imported = await repository.get(owner, trips[0].trip_id)
    assert imported.current.reason == "legacy_import"
    assert imported.current.content.snapshot == LegacyTripSnapshot(itinerary=legacy_record())
    assert not hasattr(imported.current.content.snapshot, "geometry")
    repeated = await import_legacy_jsonl(repository, source, owner, apply=True)
    assert repeated.created == 0 and repeated.skipped == 1
    assert await repository.list(owner) == trips


async def test_import_conflict_rolls_back_all_new_records(repository, tmp_path):
    owner = (await repository.create_owner("甲")).owner_id
    source = tmp_path / "old.jsonl"
    source.write_text(legacy_record().model_dump_json(), encoding="utf-8")
    await import_legacy_jsonl(repository, source, owner, apply=True)
    changed = legacy_record().model_copy(update={"title": "已修改旧行程"})
    source.write_text(legacy_record("new").model_dump_json() + "\n" + changed.model_dump_json(),
                      encoding="utf-8")
    with pytest.raises(LegacyMigrationError, match="整批"):
        await import_legacy_jsonl(repository, source, owner, apply=True)
    assert len(await repository.list(owner)) == 1


async def test_import_cannot_claim_another_owners_legacy_record(repository, tmp_path):
    alice = (await repository.create_owner("甲")).owner_id
    bob = (await repository.create_owner("乙")).owner_id
    source = tmp_path / "old.jsonl"
    source.write_text(legacy_record().model_dump_json(), encoding="utf-8")
    await import_legacy_jsonl(repository, source, alice, apply=True)
    with pytest.raises(LegacyMigrationError):
        await import_legacy_jsonl(repository, source, bob, apply=True)
    assert await repository.list(bob) == []


async def test_import_duplicate_ids_and_conflicting_backup_are_rejected(repository, tmp_path):
    owner = (await repository.create_owner("甲")).owner_id
    source = tmp_path / "old.jsonl"
    source.write_text((legacy_record().model_dump_json() + "\n") * 2, encoding="utf-8")
    report = await import_legacy_jsonl(repository, source, owner)
    assert report.duplicate_ids == ["original"]
    with pytest.raises(LegacyMigrationError):
        await import_legacy_jsonl(repository, source, owner, apply=True)
    source.write_text(legacy_record().model_dump_json(), encoding="utf-8")
    report = await import_legacy_jsonl(repository, source, owner)
    backup = source.with_name(f"{source.name}.v1-{report.source_sha256[:16]}.bak")
    backup.write_bytes(b"existing backup must not be overwritten")
    with pytest.raises(LegacyMigrationError, match="备份"):
        await import_legacy_jsonl(repository, source, owner, apply=True)
    assert backup.read_bytes() == b"existing backup must not be overwritten"
    assert await repository.list(owner) == []


def test_snapshot_models_validate_timezone_and_concrete_transit_selection():
    with pytest.raises(ValidationError):
        content().model_validate({**content().model_dump(), "timezone": "Unknown/City"})
    with pytest.raises(ValidationError):
        TransitTripSnapshot(result=direct_transit_payload(), selected_candidate_index=999)
    transit = TransitTripSnapshot(result=direct_transit_payload(), selected_candidate_index=1)
    assert transit.selected_candidate_index == 1
    with pytest.raises(ValidationError):
        PlanningTripSnapshot(result={**build_payload(), "selected_mode": "transit"})
