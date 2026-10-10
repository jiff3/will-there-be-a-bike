"""Executed against an isolated migrated PostgreSQL/PostGIS database, never SQLite."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.errors import ApplicationError
from app.main import app
from app.modules.auth.router import cookie_name
from app.modules.forecasts import service as forecast_service
from app.modules.ingestion import persistence
from app.modules.ingestion.sources import RateLimited
from app.modules.monitoring import jobs
from app.persistence.database import engine

UTC = timezone.utc  # noqa: UP017 -- native QA runner
pytestmark = pytest.mark.skipif(
    os.environ.get("APP_ENV") != "test", reason="Requires migrated PostGIS test DB"
)
ORIGIN = {"Origin": "http://localhost:5173"}


async def test_sessions_csrf_revocation_and_user_isolation(client: AsyncClient) -> None:
    suffix = uuid.uuid4().hex
    email = f"service-{suffix}@example.com"
    credentials = {"email": email, "password": "a strong unique password for tests"}
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM request_limits"))
    try:
        result = await client.post("/api/v1/auth/register", json=credentials, headers=ORIGIN)
        assert result.status_code == 201, result.text
        user = result.json()["user"]
        csrf = result.json()["csrf_token"]
        token = client.cookies.get(cookie_name())
        assert (
            "HttpOnly" in result.headers["set-cookie"]
            and "SameSite=lax" in result.headers["set-cookie"]
        )
        assert (await client.get("/api/v1/auth/me")).json()["email"] == email
        assert (await client.get("/api/v1/operations/status")).status_code == 403
        assert (
            await client.put(
                "/api/v1/auth/preferences", json={"max_walk_meters": 500}, headers=ORIGIN
            )
        ).status_code == 403
        result = await client.put(
            "/api/v1/auth/preferences",
            json={"max_walk_meters": 500},
            headers={**ORIGIN, "X-CSRF-Token": csrf},
        )
        assert result.status_code == 200, result.text
        assert (await client.get("/api/v1/auth/preferences")).json()["max_walk_meters"] == 500
        assert (
            await client.post("/api/v1/auth/csrf", headers={"Origin": "https://evil.example"})
        ).status_code == 403
        refreshed = await client.post("/api/v1/auth/csrf", headers=ORIGIN)
        assert refreshed.status_code == 200
        assert (
            await client.put(
                "/api/v1/auth/preferences",
                json={"max_walk_meters": 600},
                headers={**ORIGIN, "X-CSRF-Token": csrf},
            )
        ).status_code == 403
        csrf = refreshed.json()["csrf_token"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
            registration = await other.post(
                "/api/v1/auth/register",
                json={**credentials, "email": f"other-{suffix}@example.com"},
                headers=ORIGIN,
            )
            assert registration.status_code == 201
            assert (await other.get("/api/v1/auth/preferences")).json()["max_walk_meters"] == 1000
            spoof = await other.put(
                "/api/v1/auth/preferences",
                json={"user_id": user["id"], "max_walk_meters": 100},
                headers={**ORIGIN, "X-CSRF-Token": registration.json()["csrf_token"]},
            )
            assert spoof.status_code == 422
        login = await client.post("/api/v1/auth/login", json=credentials, headers=ORIGIN)
        assert login.status_code == 200
        assert client.cookies.get(cookie_name()) != token
        fresh_csrf = login.json()["csrf_token"]
        assert (
            await client.post("/api/v1/auth/logout", headers={**ORIGIN, "X-CSRF-Token": csrf})
        ).status_code == 403
        async with engine.connect() as connection:
            encoded = await connection.scalar(
                text("SELECT password_hash FROM users WHERE email=:email"), {"email": email}
            )
            assert encoded.startswith("$argon2id$") and credentials["password"] not in encoded
        result = await client.post(
            "/api/v1/auth/logout", headers={**ORIGIN, "X-CSRF-Token": fresh_csrf}
        )
        assert result.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        client.cookies.set(cookie_name(), str(token))
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM users WHERE email IN (:a,:b)"),
                {"a": email, "b": f"other-{suffix}@example.com"},
            )
            await connection.execute(text("DELETE FROM request_limits"))


async def test_account_rate_limit_and_expired_session(client: AsyncClient) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM request_limits"))
    for _ in range(10):
        result = await client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "wrong password long"},
            headers=ORIGIN,
        )
        assert result.status_code == 401
    limited = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong password long"},
        headers=ORIGIN,
    )
    assert limited.status_code == 429
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE request_limits SET window_start=now()-interval '16 minutes'")
        )
    reset = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong password long"},
        headers=ORIGIN,
    )
    assert reset.status_code == 401
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM request_limits"))


async def test_expired_session_is_rejected(client: AsyncClient) -> None:
    email = f"expired-{uuid.uuid4().hex}@example.com"
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM request_limits"))
    try:
        result = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "synthetic long password"},
            headers=ORIGIN,
        )
        assert result.status_code == 201
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE auth_sessions SET expires_at=now()-interval '1 second' "
                    "WHERE user_id=(SELECT id FROM users WHERE email=:email)"
                ),
                {"email": email},
            )
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE email=:email"), {"email": email})
            await connection.execute(text("DELETE FROM request_limits"))


async def test_distributed_job_exclusion_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow() -> dict[str, Any]:
        entered.set()
        await release.wait()
        return {"synthetic_test": True}

    monkeypatch.setattr(jobs, "refresh_forecasts", slow)
    first = asyncio.create_task(jobs.run_job("forecasts", force=True))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        second = await jobs.run_job("forecasts", force=True)
        assert second["status"] == "already_running"
    finally:
        release.set()
    result = await first
    assert result["status"] == "succeeded"
    assert (await jobs.run_job("forecasts"))["status"] == "not_due"
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM operation_jobs WHERE id=:id"), {"id": uuid.UUID(result["id"])}
        )


async def test_retention_removes_only_expired_sessions() -> None:
    identifier = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users(id,email,password_hash) "
                "VALUES(:id,:email,'synthetic-test-not-a-login-hash')"
            ),
            {"id": identifier, "email": f"retention-{identifier}@example.com"},
        )
        for token, expiry in (
            ("a" * 64, datetime.now(UTC) - timedelta(days=1)),
            ("b" * 64, datetime.now(UTC) + timedelta(days=1)),
        ):
            await connection.execute(
                text(
                    "INSERT INTO auth_sessions(token_hash,user_id,csrf_hash,expires_at) "
                    "VALUES(:token,:user,:csrf,:expiry)"
                ),
                {"token": token, "user": identifier, "csrf": "c" * 64, "expiry": expiry},
            )
    try:
        await jobs.retention()
        async with engine.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT token_hash FROM auth_sessions WHERE user_id=:id"),
                    {"id": identifier},
                )
            ).scalars().all() == ["b" * 64]
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE id=:id"), {"id": identifier})


async def test_failed_job_records_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail() -> dict[str, Any]:
        raise RateLimited(600)

    monkeypatch.setattr(jobs, "refresh_forecasts", fail)
    result = await jobs.run_job("forecasts", force=True)
    try:
        assert result["status"] == "failed" and result["details"]["retry_after"] >= 600
        assert (await jobs.run_job("forecasts"))["status"] == "not_due"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM operation_jobs WHERE id=:id"), {"id": uuid.UUID(result["id"])}
            )


async def test_persisted_catalog_freshness_cache_and_model_failure(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope = "integration-" + uuid.uuid4().hex
    identifiers = [scope + "-A", scope + "-B"]
    # Explicitly synthetic integration observations, removed in finally. The false
    # demo flag exercises public real-only query paths, not measured-world claims.
    catalog = {
        "data": {
            "stations": [
                {
                    "station_id": identifier,
                    "name": scope,
                    "lat": 1 + i * 0.001,
                    "lon": 1,
                    "capacity": 20,
                }
                for i, identifier in enumerate(identifiers)
            ]
        }
    }
    now = datetime.now(UTC)
    status = {
        "version": "2.3",
        "last_updated": int(now.timestamp()),
        "ttl": 60,
        "data": {
            "stations": [
                {
                    "station_id": identifier,
                    "last_reported": int((now - timedelta(minutes=i * 10)).timestamp()),
                    "num_bikes_available": 5,
                    "num_docks_available": 15,
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": True,
                }
                for i, identifier in enumerate(identifiers)
            ]
        },
    }
    try:
        await persistence.update_stations(catalog)
        await persistence.collect(status, "synthetic-integration-test")
        result = await client.get("/api/v1/stations", params={"search": scope, "limit": 1})
        assert result.status_code == 200 and result.json()["next_offset"] == 1
        result = await client.get(
            "/api/v1/stations", params={"search": scope, "available": "bikes"}
        )
        assert [row["external_id"] for row in result.json()["items"]] == [identifiers[0]]
        result = await client.get("/api/v1/stations", params={"bbox": "0.99,0.99,1.01,1.01"})
        assert len(result.json()["items"]) == 2
        result = await client.get(f"/api/v1/stations/{identifiers[1]}/availability")
        assert result.json()["freshness"] == "stale"
        result = await client.get(f"/api/v1/stations/{identifiers[0]}/predictions")
        assert result.status_code == 200, result.text
        assert all(item["confidence"] == "low" for item in result.json()["forecasts"].values())
        repeated = await client.get(f"/api/v1/stations/{identifiers[0]}/predictions")
        assert repeated.json()["forecasts"] == result.json()["forecasts"]
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM forecast_cache WHERE station_id=:id"),
                    {"id": identifiers[0]},
                )
                == 1
            )

        def unavailable() -> Any:
            raise ApplicationError("model_unavailable", "Unavailable", 503)

        monkeypatch.setattr(forecast_service, "get_forecaster", unavailable)
        assert (
            await client.get(f"/api/v1/stations/{identifiers[0]}/predictions")
        ).status_code == 503
        assert (await client.get("/api/v1/stations/not-a-station")).status_code == 404
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM forecast_cache WHERE station_id=ANY(CAST(:ids AS text[]))"),
                {"ids": identifiers},
            )
            await connection.execute(
                text(
                    "DELETE FROM availability_snapshots WHERE station_id IN"
                    "(SELECT id FROM stations WHERE external_id=ANY(CAST(:ids AS text[])))"
                ),
                {"ids": identifiers},
            )
            await connection.execute(
                text(
                    "DELETE FROM station_aliases WHERE station_id IN"
                    "(SELECT id FROM stations WHERE external_id=ANY(CAST(:ids AS text[])))"
                ),
                {"ids": identifiers},
            )
            await connection.execute(
                text("DELETE FROM stations WHERE external_id=ANY(CAST(:ids AS text[]))"),
                {"ids": identifiers},
            )
            await connection.execute(
                text("DELETE FROM feed_sync_runs WHERE source='synthetic-integration-test'")
            )
