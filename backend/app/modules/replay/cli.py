"""Publish existing coverage-aware exports and store explicit retrospective backtests."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.modules.ingestion.persistence import SYSTEM
from app.modules.ml.router import get_forecaster
from app.modules.replay.service import day_bounds
from app.persistence.database import engine
from app.persistence.rider_tables import replay_bins, replay_imports, replay_predictions


def parse_bin(line: str, allow_demo: bool = False) -> dict[str, Any]:
    row = json.loads(line)
    at = datetime.fromisoformat(row["bucket_start"])
    if (
        at.tzinfo is None
        or at.timestamp() % 900
        or type(row.get("is_demo")) is not bool
        or (row["is_demo"] and not allow_demo)
    ):
        raise ValueError("Replay requires real, aware, quarter-hour-aligned coverage exports")
    for direction, coverage in (
        ("departures", "departure_coverage"),
        ("arrivals", "arrival_coverage"),
    ):
        value = row[direction]
        if (
            type(row.get(coverage)) is not bool
            or (row[coverage] and (type(value) is not int or value < 0))
            or (value is not None and (type(value) is not int or value < 0))
        ):
            raise ValueError("Counts must agree with coverage; unknown is null, not zero")
        # The exporter can retain partial observed rides even when full directional
        # coverage is unverified. Replay suppresses those lower bounds, not zero-fills.
        if not row[coverage]:
            row[direction] = None
    return {
        "external_id": row["station_id"],
        "bucket_start": at.astimezone(UTC),
        "departures": row["departures"],
        "arrivals": row["arrivals"],
    }


async def publish(path: Path, allow_demo: bool = False) -> dict[str, Any]:
    if allow_demo and not get_settings().demo_mode:
        raise ValueError("Synthetic replay publication requires isolated DEMO_MODE=true")
    digest = hashlib.sha256()
    count = 0
    first: datetime | None = None
    last: datetime | None = None
    identities: set[str] = set()
    modes: set[bool] = set()
    # Bounded preflight: no connection held while hashing/validating a large export.
    with path.open("rb") as source:
        for line in iter(lambda: source.readline(8193), b""):
            if len(line) > 8192:
                raise ValueError("Oversized replay row")
            digest.update(line)
            row = parse_bin(line.decode("utf-8"), allow_demo)
            modes.add(json.loads(line)["is_demo"])
            at = row["bucket_start"]
            first, last = min(first or at, at), max(last or at, at)
            identities.add(row["external_id"])
            count += 1
            if count > 1_100_000 or len(identities) > 120 or (last - first).days > 93:
                raise ValueError("Replay is bounded to 120 stations / 93 days / 1.1m bins")
    if not count or first is None or last is None or len(modes) != 1:
        raise ValueError("Empty replay export")
    demo = modes.pop()
    checksum = digest.hexdigest()
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(742104)"))
        existing = await connection.scalar(
            select(replay_imports.c.id).where(replay_imports.c.source_sha256 == checksum)
        )
        if existing:
            await connection.execute(update(replay_imports).values(active=False))
            await connection.execute(
                update(replay_imports).where(replay_imports.c.id == existing).values(active=True)
            )
            return {"id": str(existing), "status": "already_published", "rows": count}
        mapping: dict[str, uuid.UUID] = dict(
            (
                await connection.execute(
                    text(
                        "SELECT external_id,id FROM stations "
                        "WHERE system_id=:system AND is_demo=:demo"
                    ),
                    {"system": SYSTEM, "demo": demo},
                )
            ).all()
        )
        if identities - mapping.keys():
            raise ValueError("Export contains station identities missing from the real catalog")
        identifier = uuid.uuid4()
        await connection.execute(
            insert(replay_imports).values(
                id=identifier,
                source_sha256=checksum,
                source_label=path.name,
                is_demo=demo,
                active=False,
                first_bucket=first,
                last_bucket=last,
                row_count=count,
            )
        )
        batch = []
        publication_digest = hashlib.sha256()
        publication_count = 0
        with path.open("rb") as source:
            for row_bytes in iter(lambda: source.readline(8193), b""):
                if len(row_bytes) > 8192:
                    raise ValueError("Replay input changed during publication")
                publication_digest.update(row_bytes)
                publication_count += 1
                if publication_count > count:
                    raise ValueError("Replay input changed during publication")
                row = parse_bin(row_bytes.decode("utf-8"), allow_demo)
                row["station_id"] = mapping[row.pop("external_id")]
                batch.append({"import_id": identifier, **row})
                if len(batch) == 500:
                    await connection.execute(insert(replay_bins), batch)
                    batch = []
            if batch:
                await connection.execute(insert(replay_bins), batch)
        if publication_count != count or publication_digest.hexdigest() != checksum:
            raise ValueError("Replay input changed during publication; transaction rolled back")
        await connection.execute(update(replay_imports).values(active=False))
        await connection.execute(
            update(replay_imports).where(replay_imports.c.id == identifier).values(active=True)
        )
    return {"id": str(identifier), "status": "published", "rows": count, "source_sha256": checksum}


async def backtest(day: date, stations: list[str]) -> dict[str, Any]:
    model = get_forecaster()
    start, end = day_bounds(day)
    if (
        start.timestamp() < model.config.boundaries()[3]
        or end.timestamp() > model.config.boundaries()[4]
    ):
        raise ValueError("Comparisons are restricted to the held-out final test period")
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(742105)"))
        dataset = (
            (await connection.execute(select(replay_imports).where(replay_imports.c.active)))
            .mappings()
            .first()
        )
        if not dataset or dataset["source_sha256"] != model.metadata["data_sha256"]:
            raise ValueError("Active replay input must match the evaluated model's source checksum")
        rows = list(
            (
                await connection.execute(
                    text("""
          SELECT b.station_id,b.bucket_start,s.external_id FROM replay_bins b
          JOIN stations s ON s.id=b.station_id WHERE b.import_id=:id
          AND b.bucket_start>=:start AND b.bucket_start<:end
          AND s.external_id=ANY(:stations) ORDER BY b.station_id,b.bucket_start
        """),
                    {"id": dataset["id"], "start": start, "end": end, "stations": stations},
                )
            ).mappings()
        )
        if len(stations) > 10 or not rows or set(stations) != {row["external_id"] for row in rows}:
            raise ValueError("Choose 1–10 supported station IDs per bounded backtest command")
        count = 0
        for row in rows:
            # No history/targets or current inventory supplied: evaluated archive-only
            # inference uses the shared sealed artifact and past training references.
            payload = model.predict(
                row["external_id"], int(row["bucket_start"].timestamp()), replay=True
            )
            result = await connection.execute(
                pg_insert(replay_predictions)
                .values(
                    import_id=dataset["id"],
                    station_id=row["station_id"],
                    as_of=row["bucket_start"],
                    model_version=model.metadata["version"],
                    payload={"forecasts": payload["forecasts"], "retrospective_replay": True},
                )
                .on_conflict_do_nothing()
            )
            count += result.rowcount
    return {
        "stored": count,
        "model_version": model.metadata["version"],
        "semantics": "retrospective held-out archive-only count forecasts, not recorded live",
    }


async def execute(args: argparse.Namespace) -> None:
    try:
        result = (
            await publish(args.data)
            if args.command == "publish"
            else await backtest(args.day, args.stations)
        )
        print(json.dumps(result))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trusted local replay publication")
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("publish")
    upload.add_argument("--data", type=Path, required=True)
    comparison = commands.add_parser("backtest")
    comparison.add_argument("--day", type=date.fromisoformat, required=True)
    comparison.add_argument("--stations", nargs="+", required=True)
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
