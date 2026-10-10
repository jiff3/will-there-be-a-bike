"""Read sealed coverage-aware counts; never imply historical station inventory."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.errors import ApplicationError
from app.persistence.database import engine

NY = ZoneInfo("America/New_York")


def day_bounds(day: date) -> tuple[datetime, datetime]:
    return datetime.combine(day, datetime.min.time(), NY).astimezone(UTC), datetime.combine(
        day + timedelta(days=1), datetime.min.time(), NY
    ).astimezone(UTC)


async def catalog() -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            (await connection.execute(text("SELECT * FROM replay_imports WHERE active")))
            .mappings()
            .first()
        )
        if not row:
            return {"dataset": None, "days": [], "timezone": "America/New_York"}
        days: Any = (
            await connection.execute(
                text(
                    "SELECT DISTINCT (bucket_start AT TIME ZONE 'America/New_York')::date "
                    "AS recorded_day "
                    "FROM replay_bins WHERE import_id=:id ORDER BY recorded_day LIMIT 94"
                ),
                {"id": row["id"]},
            )
        ).scalars()
        return {"dataset": dict(row), "days": list(days), "timezone": "America/New_York"}


async def day_data(day: date) -> dict[str, Any]:
    start, end = day_bounds(day)
    async with engine.connect() as connection:
        dataset = (
            (await connection.execute(text("SELECT * FROM replay_imports WHERE active")))
            .mappings()
            .first()
        )
        if not dataset:
            return {"dataset_id": None, "buckets": [], "stations": [], "series": []}
        rows = list(
            (
                await connection.execute(
                    text("""
          SELECT b.*,s.external_id,s.name,ST_Y(s.location::geometry) latitude,
            ST_X(s.location::geometry) longitude
          FROM replay_bins b JOIN stations s ON s.id=b.station_id
          WHERE b.import_id=:id AND b.bucket_start>=:start AND b.bucket_start<:end
          ORDER BY s.external_id,b.bucket_start LIMIT 12001
        """),
                    {"id": dataset["id"], "start": start, "end": end},
                )
            ).mappings()
        )
    if len(rows) > 12000:
        raise ApplicationError(
            "replay_scope_limit", "Replay exceeds the supported daily station scope", 409
        )
    if not rows:
        return {"dataset_id": dataset["id"], "buckets": [], "stations": [], "series": []}
    buckets = [
        start + timedelta(minutes=15 * index)
        for index in range(int((end - start).total_seconds() // 900))
    ]
    stations: dict[str, Any] = {}
    series: dict[str, Any] = {}
    for row in rows:
        key = row["external_id"]
        stations[key] = {
            field: row[field] for field in ("external_id", "name", "latitude", "longitude")
        }
        item = series.setdefault(
            key,
            {
                "station_id": key,
                "departures": [None] * len(buckets),
                "arrivals": [None] * len(buckets),
            },
        )
        index = int((row["bucket_start"] - start).total_seconds() // 900)
        item["departures"][index], item["arrivals"][index] = row["departures"], row["arrivals"]
    return {
        "dataset_id": dataset["id"],
        "buckets": buckets,
        "stations": list(stations.values()),
        "series": list(series.values()),
        "semantics": "observed rides per 15 minutes, not inventory; null means unverified coverage",
        "geography": "current catalog coordinates, not historical as-of geography",
    }


async def comparisons(station: str, day: date) -> dict[str, Any]:
    start, end = day_bounds(day)
    async with engine.connect() as connection:
        rows = list(
            (
                await connection.execute(
                    text("""
          SELECT DISTINCT ON (p.as_of) p.*,s.external_id FROM replay_predictions p
          JOIN replay_imports i ON i.id=p.import_id AND i.active
          JOIN stations s ON s.id=p.station_id
          WHERE s.external_id=:station AND p.as_of>=:start AND p.as_of<:end
          ORDER BY p.as_of,p.computed_at DESC LIMIT 100
        """),
                    {"station": station, "start": start, "end": end},
                )
            ).mappings()
        )
        actual = list(
            (
                await connection.execute(
                    text("""
          SELECT b.* FROM replay_bins b JOIN replay_imports i ON i.id=b.import_id AND i.active
          JOIN stations s ON s.id=b.station_id WHERE s.external_id=:station
          AND b.bucket_start>=:start AND b.bucket_start<CAST(:end AS timestamptz)+interval '1 hour'
          ORDER BY b.bucket_start LIMIT 104
        """),
                    {"station": station, "start": start, "end": end},
                )
            ).mappings()
        )
    by_time = {row["bucket_start"]: row for row in actual}
    items = []
    for row in rows:
        counts: dict[str, int | None] = {}
        for direction in ("departures", "arrivals"):
            for horizon, bins in ((30, 2), (60, 4)):
                subset = [
                    by_time.get(row["as_of"] + timedelta(minutes=15 * index))
                    for index in range(bins)
                ]
                counts[f"{direction}_{horizon}"] = (
                    sum(item[direction] for item in subset if item)
                    if all(item and item[direction] is not None for item in subset)
                    else None
                )
        items.append(
            {
                "as_of": row["as_of"],
                "model_version": row["model_version"],
                "computed_at": row["computed_at"],
                "forecasts": row["payload"]["forecasts"],
                "observed": counts,
            }
        )
    return {
        "items": items,
        "semantics": "retrospective held-out archive-only forecasts, computed later; "
        "not recorded live; no inventory reconstruction",
    }
