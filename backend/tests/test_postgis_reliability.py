"""Real connection interruption, transaction rollback, and observation ordering."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.ingestion import persistence
from app.modules.monitoring import jobs
from app.modules.stations import service
from app.persistence.database import engine
from app.persistence.forecast_repository import measured_inventory
from tests.test_reliability import envelope

pytestmark = pytest.mark.skipif(os.environ.get("APP_ENV") != "test", reason="Requires PostGIS")


async def test_duplicate_out_of_order_and_future_observations_preserve_latest() -> None:
    name = "QA-order-" + uuid.uuid4().hex
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=2)
    try:
        await persistence.update_stations(
            {
                "data": {
                    "stations": [
                        {
                            "station_id": name,
                            "name": name,
                            "lat": 40.75,
                            "lon": -73.98,
                            "capacity": 20,
                        }
                    ]
                }
            }
        )
        document = envelope(station_id=name, last_reported=int(now.timestamp()))
        document["last_updated"] = int(now.timestamp())
        assert (await persistence.collect(document, name))["inserted"] == 1
        assert (await persistence.collect(document, name))["duplicates"] == 1
        older = now - timedelta(minutes=20)
        document["data"]["stations"][0].update(
            last_reported=int(older.timestamp()), num_bikes_available=0
        )
        assert (await persistence.collect(document, name))["inserted"] == 1
        assert (await service.station(name))["bikes"] == 3
        future = now + timedelta(hours=1)
        document["last_updated"] = int(future.timestamp())
        document["data"]["stations"][0]["last_reported"] = int(future.timestamp())
        assert (await persistence.collect(document, name))["rejected"] == 1
        assert (await service.station(name))["bikes"] == 3
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM availability_snapshots a "
                        "JOIN stations s ON s.id=a.station_id "
                        "WHERE s.external_id=:name"
                    ),
                    {"name": name},
                )
                == 2
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM availability_snapshots WHERE sync_id IN "
                    "(SELECT id FROM feed_sync_runs WHERE source=:name)"
                ),
                {"name": name},
            )
            await connection.execute(
                text("DELETE FROM feed_sync_runs WHERE source=:name"), {"name": name}
            )
            await connection.execute(
                text("DELETE FROM station_aliases WHERE alias=:name"), {"name": name}
            )
            await connection.execute(
                text("DELETE FROM stations WHERE external_id=:name"), {"name": name}
            )


async def test_partial_catalog_failure_rolls_back_every_station() -> None:
    name = "QA-rollback-" + uuid.uuid4().hex
    with pytest.raises(ValueError, match="coordinates"):
        await persistence.update_stations(
            {
                "data": {
                    "stations": [
                        {"station_id": name, "name": name, "lat": 40, "lon": -73},
                        {"station_id": name + "-bad", "name": name, "lat": 999, "lon": -73},
                    ]
                }
            }
        )
    async with engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM stations WHERE external_id=:name"), {"name": name}
            )
            == 0
        )
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM station_aliases WHERE alias=:name"), {"name": name}
            )
            == 0
        )


async def test_real_connection_termination_rolls_back_and_pool_recovers() -> None:
    pool = create_async_engine(
        os.environ["DATABASE_URL"], pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    identifier = uuid.uuid4()
    try:
        async with pool.connect() as victim:
            pid = await victim.scalar(text("SELECT pg_backend_pid()"))
            await victim.execute(
                text("INSERT INTO users(id,email,password_hash) VALUES(:id,:email,'QA-not-login')"),
                {"id": identifier, "email": f"rollback-{identifier}@example.com"},
            )
            async with engine.begin() as killer:
                assert await killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            with pytest.raises(DBAPIError):
                await victim.execute(text("SELECT 1"))
        async with pool.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
            assert (
                await recovered.scalar(
                    text("SELECT count(*) FROM users WHERE id=:id"), {"id": identifier}
                )
                == 0
            )
    finally:
        await pool.dispose()


async def test_actual_database_refusal_returns_503_and_recovers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.stations import router
    from app.modules.stations import service as station_service

    unavailable = create_async_engine(
        "postgresql+asyncpg://qa:unused@127.0.0.1:1/bike_qa_test", connect_args={"timeout": 1}
    )
    original = station_service.engine
    try:
        monkeypatch.setattr(station_service, "engine", unavailable)
        response = await client.get("/api/v1/stations")
        assert response.status_code == 503, response.text
        assert "unused" not in response.text
    finally:
        monkeypatch.setattr(station_service, "engine", original)
        await unavailable.dispose()
    assert (await router.catalog(1, 0, "", None))["limit"] == 1


async def test_cancelled_job_releases_lock_and_next_job_marks_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def stalled() -> dict[str, Any]:
        entered.set()
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(jobs, "refresh_forecasts", stalled)
    task = asyncio.create_task(jobs.run_job("forecasts", force=True))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async def recovered() -> dict[str, Any]:
        return {"synthetic_qa_recovered": True}

    monkeypatch.setattr(jobs, "refresh_forecasts", recovered)
    result = await jobs.run_job("forecasts", force=True)
    assert result["status"] == "succeeded"
    async with engine.begin() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM operation_jobs "
                    "WHERE kind='forecasts' AND status='abandoned'"
                )
            )
            >= 1
        )
        await connection.execute(text("DELETE FROM operation_jobs WHERE kind='forecasts'"))


async def test_real_forecast_refresh_and_operator_authorization(client: AsyncClient) -> None:
    # Actual sealed artifact, actual bounded SQL and persistent job status.
    result = await jobs.run_job("forecasts", force=True)
    assert result["status"] == "succeeded", result
    assert 0 <= result["details"]["station_count"] <= 120
    assert (await client.get("/api/v1/operations/status")).status_code == 401
    email = f"qa-admin-{uuid.uuid4().hex}@example.com"
    try:
        registration = await client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={"email": email, "password": "Disposable admin QA password!"},
        )
        assert registration.status_code == 201
        assert (await client.get("/api/v1/operations/status")).status_code == 403
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE users SET is_admin=true WHERE email=:email"), {"email": email}
            )
        status = await client.get("/api/v1/operations/status")
        assert status.status_code == 200
        assert any(row["id"] == result["id"] for row in status.json()["jobs"])
        assert "password_hash" not in status.text
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE email=:email"), {"email": email})
            await connection.execute(
                text("DELETE FROM operation_jobs WHERE id=:id"), {"id": uuid.UUID(result["id"])}
            )


async def test_failed_live_job_keeps_measurements_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "QA-feed-" + uuid.uuid4().hex
    catalog = {
        "data": {
            "stations": [
                {"station_id": name, "name": name, "lat": 40.75, "lon": -73.98, "capacity": 20}
            ]
        }
    }
    now = datetime.now(UTC).replace(microsecond=0)
    document = envelope(station_id=name, last_reported=int(now.timestamp()))
    document["last_updated"] = int(now.timestamp())
    identifiers = []
    try:
        await persistence.update_stations(catalog)
        await persistence.collect(document, name)
        assert await measured_inventory(name, now - timedelta(seconds=1)) is None
        assert (await measured_inventory(name, datetime.now(UTC)))["bikes"] == 3

        def timeout():
            raise OSError("injected GBFS failure, sensitive provider detail")

        monkeypatch.setattr(jobs, "fetch_live", timeout)
        failed = await jobs.run_job("live", force=True)
        identifiers.append(uuid.UUID(failed["id"]))
        assert failed["status"] == "failed"
        assert "sensitive" not in str(failed["details"])
        assert (await service.station(name))["bikes"] == 3

        document["data"]["stations"][0]["num_bikes_available"] = -1
        monkeypatch.setattr(jobs, "fetch_live", lambda: (catalog, document, name))
        invalid = await jobs.run_job("live", force=True)
        identifiers.append(uuid.UUID(invalid["id"]))
        assert invalid["status"] == "failed"  # No silent success for entirely invalid status.
        assert (await service.station(name))["bikes"] == 3
        document["data"]["stations"][0]["num_bikes_available"] = 3
        recovered = await jobs.run_job("live", force=True)
        identifiers.append(uuid.UUID(recovered["id"]))
        assert recovered["status"] == "succeeded" and recovered["details"]["duplicates"] == 1
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM operation_jobs WHERE id=ANY(:ids)"), {"ids": identifiers}
            )
            await connection.execute(
                text(
                    "DELETE FROM availability_snapshots WHERE sync_id IN "
                    "(SELECT id FROM feed_sync_runs WHERE source=:name)"
                ),
                {"name": name},
            )
            await connection.execute(
                text("DELETE FROM feed_sync_runs WHERE source=:name"), {"name": name}
            )
            await connection.execute(
                text("DELETE FROM station_aliases WHERE alias=:name"), {"name": name}
            )
            await connection.execute(
                text("DELETE FROM stations WHERE external_id=:name"), {"name": name}
            )


async def test_historical_demand_real_sql_sparse_counts_and_range_validation(
    client: AsyncClient,
) -> None:
    scope = "QA-history-" + uuid.uuid4().hex
    await persistence.update_stations(
        {"data": {"stations": [{"station_id": scope, "name": scope, "lat": 40.75, "lon": -73.98}]}}
    )
    start = datetime(2025, 2, 28, 13, tzinfo=UTC)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO historical_trips VALUES(:ride,:start,:end,:station,:station,false)"),
            {"ride": scope, "start": start, "end": start + timedelta(minutes=15), "station": scope},
        )
    try:
        response = await client.get(
            f"/api/v1/stations/{scope}/demand",
            params={"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()},
        )
        assert response.status_code == 200
        observations = response.json()["observations"]
        assert [(row["departures"], row["arrivals"]) for row in observations] == [(1, 0), (0, 1)]
        assert "not_returned_and_not_assumed_zero" in response.json()["missing_bins"]
        assert (
            await client.get(
                f"/api/v1/stations/{scope}/demand",
                params={
                    "start": start.isoformat(),
                    "end": (start + timedelta(seconds=1)).isoformat(),
                },
            )
        ).status_code == 422
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM historical_trips WHERE ride_id=:id"), {"id": scope}
            )
            await connection.execute(
                text("DELETE FROM station_aliases WHERE alias=:id"), {"id": scope}
            )
            await connection.execute(
                text("DELETE FROM stations WHERE external_id=:id"), {"id": scope}
            )
