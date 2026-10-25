"""Explicit, repeatable v1 import; normal startup never migrates user data."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.domain.trip import LegacyImportReport, LegacyTripSnapshot, TripContent
from travel_agent.services.trip_repository import TripRepository, TripStorageError


class LegacyMigrationError(TripStorageError):
    """A source conflict or invalid record prevents the entire import."""


def _record_digest(record: SavedItinerary) -> str:
    return hashlib.sha256(record.model_dump_json().encode()).hexdigest()


def _scan_source(
    source: Path, destination: Path,
) -> tuple[bytes, list[SavedItinerary], LegacyImportReport]:
    if source.resolve() == destination.resolve():
        raise LegacyMigrationError("源文件和目标数据库不能相同")
    raw = source.read_bytes()
    records: dict[str, SavedItinerary] = {}
    invalid = []
    duplicates = []
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise LegacyMigrationError("旧行程文件不是有效 UTF-8，未导入") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = SavedItinerary.model_validate_json(line)
        except ValidationError:
            invalid.append(line_number)
            continue
        if record.itinerary_id in records:
            duplicates.append(record.itinerary_id)
        else:
            records[record.itinerary_id] = record
    return raw, list(records.values()), LegacyImportReport(
        source_sha256=hashlib.sha256(raw).hexdigest(), valid_records=len(records),
        invalid_lines=invalid, duplicate_ids=duplicates, checked_at=datetime.now(UTC),
    )


async def import_legacy_jsonl(
    repository: TripRepository, source: Path, owner_id: UUID, *, apply: bool = False,
) -> LegacyImportReport:
    """Dry-run is read-only. Apply requires a pre-created owner and initialized DB."""
    raw, records, report = await asyncio.to_thread(_scan_source, source, repository.storage_path)
    if not apply:
        return report
    if report.invalid_lines or report.duplicate_ids:
        raise LegacyMigrationError("旧文件包含无效行或重复行程 ID，请先处理；本次未导入")
    return await asyncio.to_thread(_apply, repository, source, owner_id, raw, records, report)


def _apply(
    repository: TripRepository, source: Path, owner_id: UUID, raw: bytes,
    records: list[SavedItinerary], report: LegacyImportReport,
) -> LegacyImportReport:
    backup = source.with_name(f"{source.name}.v1-{report.source_sha256[:16]}.bak")
    try:
        # Exclusive creation protects an existing backup; never overwrite the source.
        with backup.open("xb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError:
        if backup.read_bytes() != raw:
            raise LegacyMigrationError("已有备份与源快照不符，未导入") from None
    report.backup_path = str(backup.resolve())
    with repository._database(write=True) as database:
        if database.execute("SELECT 1 FROM trip_owners WHERE owner_id=?",
                            (str(owner_id),)).fetchone() is None:
            raise LegacyMigrationError("请先明确创建迁移归属用户，未导入")
        for record in records:
            existing = database.execute(
                "SELECT * FROM legacy_trip_imports WHERE legacy_id=?", (record.itinerary_id,),
            ).fetchone()
            digest = _record_digest(record)
            if existing is not None:
                if existing["owner_id"] != str(owner_id) or existing["record_sha256"] != digest:
                    raise LegacyMigrationError("已导入行程的归属或内容发生冲突，整批导入已回滚")
                report.skipped += 1
                continue
            content = TripContent(title=record.title, snapshot=LegacyTripSnapshot(itinerary=record))
            trip_id = repository._insert_trip(database, owner_id, content, "legacy_import")
            database.execute(
                "INSERT INTO legacy_trip_imports VALUES (?, ?, ?, ?, ?)",
                (record.itinerary_id, str(owner_id), str(trip_id), report.source_sha256, digest),
            )
            report.created += 1
    return report
