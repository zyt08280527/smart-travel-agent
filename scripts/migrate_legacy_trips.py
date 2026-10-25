"""Inspect or explicitly import a v1 JSONL into a separate v2 business database."""

import argparse
import asyncio
from pathlib import Path
from uuid import UUID

from travel_agent.services.legacy_trip_migration import import_legacy_jsonl
from travel_agent.services.trip_repository import TripRepository


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--owner-name", default="本地旧行程所有者")
    parser.add_argument("--apply", action="store_true", help="默认只检查；此参数才写入")
    args = parser.parse_args()
    repository = TripRepository(args.database)
    # Validate before initializing or creating an owner, including --apply mode.
    report = await import_legacy_jsonl(repository, args.source, args.owner_id)
    if args.apply:
        if report.invalid_lines or report.duplicate_ids:
            parser.error("源文件包含无效行或重复 ID，请先运行只读检查并修复")
        await repository.initialize()
        await repository.create_owner(args.owner_name, args.owner_id)
        report = await import_legacy_jsonl(repository, args.source, args.owner_id, apply=True)
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
