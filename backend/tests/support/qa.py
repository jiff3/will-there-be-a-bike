"""Guarded deterministic PostGIS fixture driver for full-stack E2E and load tests.

All inserted values are SYNTHETIC QA observations, not measurements. The non-demo
query path is exercised only in a dedicated *_test database with APP_ENV=test.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from sqlalchemy import text

from app.core.config import get_settings
from app.modules.ingestion.history import Stage
from app.modules.ingestion.persistence import collect, update_stations
from app.modules.ingestion.persistence import publish as publish_trips
from app.modules.monitoring.jobs import run_job
from app.modules.replay.cli import publish
from app.persistence.database import engine

PICKUP = "05c5e17c-7aa9-49b7-9da3-9db4858ec1fc"
DESTINATION = "06109e92-4363-445a-9743-0457f6799530"


def guard() -> None:
    settings = get_settings()
    name = urlsplit(settings.database_url).path.lstrip("/")
    if settings.app_env != "test" or not name.endswith("_test"):
        raise RuntimeError("QA fixtures require APP_ENV=test and an isolated *_test database")
    if settings.background_jobs_enabled:
        raise RuntimeError("Disable background provider jobs for deterministic QA")


async def observations(stale: bool = False) -> dict[str, int]:
    guard()
    now = datetime.now(UTC)
    # Freshness is mutable only in this guarded, disposable database.
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM availability_snapshots"))
    return await collect(
        {
            "version": "2.3",
            "last_updated": int((now - timedelta(minutes=10 if stale else 0)).timestamp()),
            "ttl": 60,
            "data": {
                "stations": [
                    {
                        "station_id": row,
                        "last_reported": int(
                            (now - timedelta(minutes=10 if stale else 0)).timestamp()
                        ),
                        "num_bikes_available": 2,
                        "num_docks_available": 18,
                        "is_installed": True,
                        "is_renting": True,
                        "is_returning": True,
                    }
                    for row in [PICKUP, DESTINATION, *(f"QA-{i:03}" for i in range(2, 100))]
                ]
            },
        },
        "SYNTHETIC QA ONLY - no provider measurement",
    )


def trip_fixture(first: datetime) -> str:
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(
        [
            "ride_id",
            "started_at",
            "ended_at",
            "start_station_id",
            "end_station_id",
            "start_lat",
            "start_lng",
            "end_lat",
            "end_lng",
        ]
    )
    for station in (PICKUP, DESTINATION):
        for i in range(96):
            at = first + timedelta(minutes=i * 15)
            for j in range(i % 4):
                writer.writerow(
                    [
                        f"QA-DEP-{station}-{i}-{j}",
                        at.isoformat(),
                        (at + timedelta(minutes=5)).isoformat(),
                        station,
                        "QA-OUTSIDE",
                        40.751,
                        -73.986,
                        40.75,
                        -73.98,
                    ]
                )
            for j in range((i + 1) % 3):
                writer.writerow(
                    [
                        f"QA-ARR-{station}-{i}-{j}",
                        (at - timedelta(minutes=5)).isoformat(),
                        at.isoformat(),
                        "QA-OUTSIDE",
                        station,
                        40.75,
                        -73.98,
                        40.751,
                        -73.986,
                    ]
                )
    return content.getvalue()


async def seed() -> dict[str, object]:
    guard()
    await update_stations(
        {
            "data": {
                "stations": [
                    {
                        "station_id": PICKUP if i == 0 else DESTINATION if i == 1 else f"QA-{i:03}",
                        "name": "QA Pickup"
                        if i == 0
                        else "QA Destination"
                        if i == 1
                        else f"QA Station {i:03}",
                        "lat": 40.751 + (i // 10) * 0.0006,
                        "lon": -73.986 + (i % 10) * 0.0006,
                        "capacity": 20,
                    }
                    for i in range(100)
                ]
            }
        }
    )
    counts = await observations()
    first = datetime(2025, 2, 28, 5, tzinfo=UTC)
    with TemporaryDirectory(prefix="bike-qa-") as folder:
        # Actual ZIP -> chunked staging -> provenance -> aggregation. These are
        # authored test rides, NEVER official source observations or ML metrics.
        archive = Path(folder) / "SYNTHETIC-QA.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            info = zipfile.ZipInfo("SYNTHETIC-QA.csv", date_time=(2025, 2, 28, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, trip_fixture(first))
        stage = Stage(Path(folder) / "stage.sqlite3")
        try:
            run = stage.ingest(
                archive,
                "2025-02-27",
                "2025-03-01",
                {PICKUP, DESTINATION},
                chunk_size=50,
                source="SYNTHETIC QA fixture, not official trip data",
            )
            await publish_trips(stage, run)
            rows = list(stage.features("2025-02-28", "2025-03-01", {PICKUP, DESTINATION}))
        finally:
            stage.db.close()
        path = Path(folder) / "SYNTHETIC-QA-NOT-MEASUREMENTS.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for row in rows:
                # The author knows this finite generated QA universe is complete.
                # Stage correctly refuses to assert real coverage for an unknown
                # source label. This fixture-only declaration NEVER runs on real data.
                row.update(
                    departure_coverage=True,
                    arrival_coverage=True,
                    departures=row["departures"] or 0,
                    arrivals=row["arrivals"] or 0,
                )
                stream.write(json.dumps(row) + "\n")
        replay = await publish(path)
    return {"stations": 100, "observations": counts, "replay": replay, "synthetic_qa_only": True}


async def execute(command: str) -> None:
    guard()
    try:
        if command == "seed":
            result = await seed()
        elif command in {"fresh", "stale"}:
            result = await observations(command == "stale")
        elif command == "empty-replay":
            async with engine.begin() as connection:
                await connection.execute(text("UPDATE replay_imports SET active=false"))
            result = {"empty_qa_replay": True}
        elif command == "clean-accounts":
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM users WHERE email LIKE 'qa-browser-%@example.com' "
                        "OR email LIKE 'qa-outsider-%@example.com'"
                    )
                )
                await connection.execute(text("DELETE FROM request_limits"))
            result = {"disposable_browser_accounts_removed": True}
        else:
            await observations()
            result = await run_job("alerts", force=True)
        print(json.dumps(result))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["seed", "alerts", "fresh", "stale", "empty-replay", "clean-accounts"]
    )
    asyncio.run(execute(parser.parse_args().command))


if __name__ == "__main__":
    main()
