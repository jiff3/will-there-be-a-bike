"""In-process scheduled jobs coordinated across workers by PostgreSQL advisory locks."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.modules.commutes.alerts import evaluate_alerts
from app.modules.forecasts.service import count_forecast
from app.modules.ingestion.live import walking_distance
from app.modules.ingestion.persistence import collect, update_stations
from app.modules.ingestion.sources import SourceClient
from app.persistence.database import engine

UTC = timezone.utc  # noqa: UP017 -- native QA runner
logger = logging.getLogger(__name__)
LOCKS = {"live": 742100, "alerts": 742103, "forecasts": 742101, "retention": 742102}


def fetch_live() -> tuple[dict[str, Any], dict[str, Any], str]:
    source = SourceClient()
    try:
        feeds = source.feeds(get_settings().gbfs_discovery_url)
        return (
            source.get(feeds["station_information"]).json(),
            source.get(feeds["station_status"]).json(),
            feeds["station_status"],
        )
    finally:
        source.client.close()


async def live_update() -> dict[str, Any]:
    catalog, status, url = await run_in_threadpool(fetch_live)
    async with engine.connect() as connection:
        identifiers: set[str] = set(
            (
                await connection.execute(
                    text("SELECT external_id FROM stations WHERE NOT is_demo AND is_active")
                )
            ).scalars()
        )
    rows = catalog["data"]["stations"]
    if identifiers:
        rows = [row for row in rows if row["station_id"] in identifiers]
    else:
        rows = sorted(
            rows, key=lambda row: walking_distance(40.748, -73.985, row["lat"], row["lon"])
        )[:100]
    if not rows:
        raise ValueError("Provider catalog has no selected station identities")
    catalog["data"]["stations"] = rows
    await update_stations(catalog)
    selected = {row["station_id"] for row in rows}
    status["data"]["stations"] = [
        row for row in status["data"]["stations"] if row["station_id"] in selected
    ]
    if not status["data"]["stations"]:
        raise ValueError("Provider status has no selected stations")
    result = await collect(status, url)
    if result["inserted"] + result["duplicates"] == 0:
        raise ValueError("Provider returned no valid selected station observations")
    return {**result, "scope_stations": len(rows), "publisher_ttl": status["ttl"]}


async def refresh_forecasts() -> dict[str, Any]:
    async with engine.connect() as connection:
        stations: list[str] = list(
            (
                await connection.execute(
                    text(
                        "SELECT external_id FROM stations WHERE NOT is_demo AND is_active "
                        "ORDER BY external_id LIMIT 120"
                    )
                )
            ).scalars()
        )
    now = datetime.now(UTC)
    at = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    version = None
    for station in stations:
        payload = await count_forecast(station, at)
        version = payload["model_version"]
    return {"station_count": len(stations), "model_version": version, "as_of": at.isoformat()}


async def retention() -> dict[str, Any]:
    settings = get_settings()
    removed = {}
    policies = {
        "availability_snapshots": ("fetched_at", settings.snapshot_retention_days),
        "auth_sessions": ("expires_at", 0),
        "request_limits": ("window_start", 1),
        "forecast_cache": ("expires_at", 0),
        "operation_jobs": ("finished_at", settings.job_retention_days),
        "notifications": ("created_at", 90),
    }
    for table, (column, days) in policies.items():
        async with engine.begin() as connection:
            result = await connection.execute(
                text(f"""
              WITH old AS (SELECT ctid FROM {table} WHERE {column}<now()-make_interval(days=>:days)
                ORDER BY {column} LIMIT 10000 FOR UPDATE SKIP LOCKED)
              DELETE FROM {table} WHERE ctid IN (SELECT ctid FROM old)
            """),
                {"days": days},
            )
            removed[table] = result.rowcount
    async with engine.begin() as connection:
        result = await connection.execute(
            text("""
          WITH old AS (SELECT f.id FROM feed_sync_runs f
            WHERE f.fetched_at<now()-make_interval(days=>:days)
              AND NOT EXISTS(SELECT 1 FROM availability_snapshots s WHERE s.sync_id=f.id)
            ORDER BY f.fetched_at LIMIT 10000 FOR UPDATE SKIP LOCKED)
          DELETE FROM feed_sync_runs WHERE id IN(SELECT id FROM old)
        """),
            {"days": settings.snapshot_retention_days},
        )
        removed["unreferenced_feed_sync_runs"] = result.rowcount
    return {"deleted": removed, "batch_limit_per_table": 10000}


async def run_job(kind: str, force: bool = False) -> dict[str, Any]:
    functions: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
        "live": live_update,
        "forecasts": refresh_forecasts,
        "retention": retention,
        "alerts": evaluate_alerts,
    }
    intervals = {
        "live": get_settings().gbfs_poll_seconds,
        "alerts": 60,
        "forecasts": 900,
        "retention": 3600,
    }
    async with engine.connect() as lock:
        acquired = await lock.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCKS[kind]}
        )
        await lock.commit()
        if not acquired:
            return {"status": "already_running", "kind": kind}
        identifier = uuid.uuid4()
        try:
            last = (
                (
                    await lock.execute(
                        text("""
              SELECT started_at,details FROM operation_jobs WHERE kind=:kind
              ORDER BY started_at DESC LIMIT 1
            """),
                        {"kind": kind},
                    )
                )
                .mappings()
                .first()
            )
            await lock.commit()
            details = (
                json.loads(last["details"])
                if last and isinstance(last["details"], str)
                else last["details"]
                if last
                else {}
            )
            interval = max(
                intervals[kind],
                min(86400, int(details.get("publisher_ttl", 0))),
                min(86400, int(details.get("retry_after", 0))),
            )
            if (
                last
                and not force
                and (datetime.now(UTC) - last["started_at"]).total_seconds() < interval
            ):
                return {"status": "not_due", "kind": kind}
            async with engine.begin() as connection:
                await connection.execute(
                    text("""
                  UPDATE operation_jobs SET status='abandoned',finished_at=now()
                  WHERE kind=:kind AND status='running'
                """),
                    {"kind": kind},
                )
                await connection.execute(
                    text("INSERT INTO operation_jobs(id,kind,status) VALUES(:id,:kind,'running')"),
                    {"id": identifier, "kind": kind},
                )
            prior_failures = int(details.get("consecutive_failures", 0))
            try:
                details = await functions[kind]()
                status = "succeeded"
            except Exception as error:
                failures = prior_failures + 1
                details = {
                    "error_type": type(error).__name__,
                    "consecutive_failures": failures,
                    "retry_after": max(
                        int(getattr(error, "retry_after", 0)), min(900, 60 * 2 ** min(failures, 4))
                    ),
                }
                status = "failed"
                logger.warning("background_job_failed", extra={"kind": kind, **details})
            async with engine.begin() as connection:
                await connection.execute(
                    text("""
                  UPDATE operation_jobs SET status=:status,finished_at=now(),
                  details=CAST(:details AS jsonb) WHERE id=:id
                """),
                    {"id": identifier, "status": status, "details": json.dumps(details)},
                )
            return {"id": str(identifier), "kind": kind, "status": status, "details": details}
        finally:
            # Dedicated checked-out connection owns the session lock; no pool leakage.
            await lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCKS[kind]})
            await lock.commit()


async def worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        for kind in LOCKS:
            if stop.is_set():
                break
            try:
                await run_job(kind)
            except Exception as error:
                logger.warning(
                    "job_dependency_unavailable",
                    extra={"kind": kind, "error_type": type(error).__name__},
                )
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:  # noqa: UP041
            pass
