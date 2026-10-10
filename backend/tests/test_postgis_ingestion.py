"""Real PostGIS contracts; CI supplies DATABASE_URL and migrated schema."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.ingestion import persistence
from app.modules.ingestion.history import Stage
from tests.test_ingestion import FIXTURES, archive


@pytest.mark.skipif(os.environ.get("APP_ENV") != "test", reason="Requires migrated PostGIS test DB")
async def test_database_publication_and_spatial_inventory(tmp_path: Path) -> None:
    db = create_async_engine(os.environ["DATABASE_URL"])
    previous = persistence.engine
    persistence.engine = db
    try:
        await persistence.update_stations(
            json.loads((FIXTURES / "stations.json").read_text()), True
        )
        document = json.loads((FIXTURES / "status.json").read_text())
        first = await persistence.collect(document, "synthetic-fixture", True)
        second = await persistence.collect(document, "synthetic-fixture", True)
        assert first["inserted"] == 2 and second["inserted"] == 0
        stations = await persistence.nearby(40.748, -73.985, 1000, 10, True)
        demo_stations = [s for s in stations if s["is_demo"]]
        assert demo_stations[0]["walking_meters"] == 0
        assert demo_stations[0]["bikes"] == 5 and demo_stations[0]["stale"]
        assert not any(s["is_demo"] for s in await persistence.nearby(40.748, -73.985, 1000, 10))
        stage = Stage(tmp_path / "stage.db")
        run = stage.ingest(
            archive(tmp_path), "2025-01-01", "2025-01-02", {"DEMO-A", "DEMO-B"}, demo=True
        )
        await persistence.publish(stage, run)
        await persistence.publish(stage, run)
        partial = stage.ingest(
            archive(tmp_path),
            "2025-01-01",
            "2025-01-03",
            {"DEMO-A", "DEMO-B"},
            demo=True,
            max_rows=1,
        )
        with pytest.raises(ValueError, match="Only complete"):
            await persistence.publish(stage, partial)
        with pytest.raises(IntegrityError):
            async with db.begin() as connection:
                await connection.execute(
                    text("UPDATE availability_snapshots SET bikes=-1 WHERE is_demo")
                )
        with pytest.raises(IntegrityError):
            async with db.begin() as connection:
                await connection.execute(text("DELETE FROM stations WHERE external_id='DEMO-A'"))
        async with db.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT count(*) FROM trip_provenance WHERE run_id=:id"), {"id": run}
                )
            ).scalar() == 2
            assert (
                await connection.execute(
                    text("SELECT sum(departures) FROM historical_demand WHERE run_id=:id"),
                    {"id": run},
                )
            ).scalar() == 2
        stage.db.close()
    finally:
        async with db.begin() as connection:
            for table in ("historical_demand", "trip_provenance"):
                await connection.execute(
                    text(
                        f"DELETE FROM {table} WHERE run_id IN "
                        "(SELECT id FROM ingestion_runs WHERE is_demo)"
                    )
                )
            await connection.execute(text("DELETE FROM historical_trips WHERE is_demo"))
            await connection.execute(text("DELETE FROM ingestion_runs WHERE is_demo"))
            await connection.execute(text("DELETE FROM availability_snapshots WHERE is_demo"))
            await connection.execute(text("DELETE FROM feed_sync_runs WHERE is_demo"))
            await connection.execute(
                text(
                    "DELETE FROM station_aliases WHERE station_id IN "
                    "(SELECT id FROM stations WHERE is_demo)"
                )
            )
            await connection.execute(text("DELETE FROM stations WHERE is_demo"))
        persistence.engine = previous
        await db.dispose()
