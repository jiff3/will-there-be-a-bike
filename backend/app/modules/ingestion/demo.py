"""Small, repeatable, explicitly synthetic offline application workspace.

Never run this on a populated real-data database. Uses normal ingestion/replay
services, not HTTP mocks or unlabelled production-like QA observations.
"""

import argparse
import asyncio
import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.modules.ingestion.history import Stage
from app.modules.ingestion.persistence import collect, publish, update_stations
from app.modules.replay.cli import publish as publish_replay
from app.persistence.database import engine


def rides() -> str:
    output = io.StringIO()
    writer = csv.writer(output)
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
    coordinates = {"DEMO-A": (40.748, -73.985), "DEMO-B": (40.752, -73.978)}
    first = datetime(2025, 2, 28, 5, tzinfo=UTC)
    for i in range(96):
        for origin, destination, number in [
            ("DEMO-A", "DEMO-B", (i // 8) % 5),
            ("DEMO-B", "DEMO-A", (i // 12) % 4),
        ]:
            for j in range(number):
                at = first + timedelta(minutes=i * 15, seconds=j * 60)
                writer.writerow(
                    [
                        f"SYNTHETIC-{origin}-{i}-{j}",
                        at.isoformat(),
                        (at + timedelta(minutes=8)).isoformat(),
                        origin,
                        destination,
                        *coordinates[origin],
                        *coordinates[destination],
                    ]
                )
    return output.getvalue()


async def seed(directory: Path) -> dict[str, object]:
    settings = get_settings()
    if not settings.demo_mode or settings.background_jobs_enabled:
        raise ValueError("Use an isolated DEMO_MODE=true workspace with provider jobs disabled")
    async with engine.connect() as connection:
        if await connection.scalar(text("SELECT EXISTS(SELECT 1 FROM stations WHERE NOT is_demo)")):
            raise ValueError("Refusing to seed demo into a real-station database")
    directory.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 -- standalone CLI, not serving
    catalog = {
        "data": {
            "stations": [
                {
                    "station_id": "DEMO-A",
                    "name": "Demo · Midtown pickup",
                    "lat": 40.748,
                    "lon": -73.985,
                    "capacity": 20,
                },
                {
                    "station_id": "DEMO-B",
                    "name": "Demo · East-side destination",
                    "lat": 40.752,
                    "lon": -73.978,
                    "capacity": 20,
                },
            ]
        }
    }
    await update_stations(catalog, demo=True)
    now = int(datetime.now(UTC).timestamp())
    await collect(
        {
            "version": "2.3",
            "last_updated": now,
            "ttl": 60,
            "data": {
                "stations": [
                    {
                        "station_id": station,
                        "last_reported": now,
                        "num_bikes_available": bikes,
                        "num_docks_available": 20 - bikes,
                        "is_installed": True,
                        "is_renting": True,
                        "is_returning": True,
                    }
                    for station, bikes in [("DEMO-A", 5), ("DEMO-B", 2)]
                ]
            },
        },
        "SYNTHETIC OFFLINE DEMO - not provider measurements",
        demo=True,
    )
    archive = directory / "synthetic-demo.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        info = zipfile.ZipInfo("synthetic-trips.csv", date_time=(2025, 2, 28, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zipped.writestr(info, rides())
    stage = Stage(directory / "stage.sqlite3")
    try:
        run = stage.ingest(
            archive,
            "2025-02-27",
            "2025-03-01",
            {"DEMO-A", "DEMO-B"},
            chunk_size=50,
            source="SYNTHETIC OFFLINE DEMO",
            demo=True,
        )
        await publish(stage, run)
        features = directory / "synthetic-demo.jsonl"
        with features.open("w", encoding="utf-8") as output:
            for row in stage.features("2025-02-28", "2025-03-01", {"DEMO-A", "DEMO-B"}):
                output.write(json.dumps(row) + "\n")
        replay = await publish_replay(features, allow_demo=True)
    finally:
        stage.db.close()
    archive.unlink()  # Owned generated archive is redundant after a completed checkpoint.
    return {
        "is_demo": True,
        "stations": 2,
        "trip_run": run,
        "replay": replay,
        "forecasts": "unvalidated pooled historical reference; no calibrated availability risk",
    }


async def execute(directory: Path) -> None:
    try:
        print(json.dumps(await seed(directory)))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/demo"))
    asyncio.run(execute(parser.parse_args().data_dir))


if __name__ == "__main__":
    main()
