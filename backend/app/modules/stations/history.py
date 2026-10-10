"""Sparse observed rides from normalized persisted trips, not reconstructed inventory."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.persistence.database import engine


async def observed_demand(station: str, start: datetime, end: datetime) -> dict[str, Any]:
    demo = get_settings().demo_mode
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("""
          SELECT date_bin('15 minutes',at,'2000-01-01'::timestamptz) bucket_start,
                 sum(departures) departures,sum(arrivals) arrivals
          FROM (
            SELECT started_at at,1 departures,0 arrivals FROM historical_trips
            WHERE start_station=:station AND started_at>=:start
              AND started_at<:end AND is_demo=:demo
            UNION ALL
            SELECT ended_at,0,1 FROM historical_trips
            WHERE end_station=:station AND ended_at>=:start AND ended_at<:end AND is_demo=:demo
          ) rides GROUP BY bucket_start ORDER BY bucket_start
        """),
                {"station": station, "start": start, "end": end, "demo": demo},
            )
        ).mappings()
        points = [dict(row) for row in rows]
        runs = (
            await connection.execute(
                text("""
          SELECT id,source,checksum,scope FROM ingestion_runs
          WHERE is_demo=:demo AND state='complete'
          AND scope->'stations' ? :station ORDER BY published_at DESC LIMIT 12
        """),
                {"station": station, "demo": demo},
            )
        ).mappings()
        provenance = [dict(row) for row in runs]
    return {
        "station_id": station,
        "start": start,
        "end": end,
        "observations": points,
        "sources": provenance,
        "bucket_minutes": 15,
        "is_demo": demo,
        "missing_bins": (
            "not_returned_and_not_assumed_zero; use coverage-aware exporter for model inputs"
        ),
        "semantics": "valid observed rides only, not historical inventory or stockout outcomes",
    }
