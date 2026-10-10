"""Restricted status read model; callers enforce operator authorization."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.persistence.database import engine


async def status_records() -> dict[str, Any]:
    queries = {
        "jobs": "SELECT * FROM operation_jobs ORDER BY started_at DESC LIMIT 20",
        "feeds": "SELECT fetched_at,feed_updated_at,stations_seen,rejected,is_demo "
        "FROM feed_sync_runs ORDER BY fetched_at DESC LIMIT 5",
        "ingestion_runs": "SELECT id,state,rows_seen,invalid,is_demo,published_at "
        "FROM ingestion_runs ORDER BY published_at DESC LIMIT 10",
    }
    records = {}
    async with engine.connect() as connection:
        for key, query in queries.items():
            records[key] = [dict(row) for row in (await connection.execute(text(query))).mappings()]
    for job in records["jobs"]:
        if isinstance(job["details"], str):
            job["details"] = json.loads(job["details"])
    return {**records, "pool": {"status": engine.pool.status()}}
