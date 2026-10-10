"""Transactional publication and PostGIS queries owned by ingestion services."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from itertools import islice
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.modules.ingestion.history import Stage
from app.modules.ingestion.live import status_records, timestamp
from app.persistence.database import engine

UTC = timezone.utc  # noqa: UP017 -- local Python 3.10 verification
SYSTEM = uuid.uuid5(uuid.NAMESPACE_URL, "https://citibikenyc.com")


async def update_stations(document: dict[str, Any], demo: bool = False) -> int:
    async with engine.begin() as connection:
        await connection.execute(
            text("""INSERT INTO bike_share_systems(id,slug,name,timezone)
          VALUES(:id,'citibike','Citi Bike','America/New_York') ON CONFLICT DO NOTHING"""),
            {"id": SYSTEM},
        )
        count = 0
        for station in document["data"]["stations"]:
            lat, lon = float(station["lat"]), float(station["lon"])
            if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ValueError("Invalid station coordinates")
            external = str(station["station_id"])
            identifier = uuid.uuid5(SYSTEM, external + str(demo))
            name = station["name"]
            if isinstance(name, list):
                name = name[0]["text"]
            station_result = await connection.execute(
                text("""INSERT INTO stations
              (id,system_id,external_id,name,capacity,location,is_demo) VALUES
              (:id,:system,:external,:name,:capacity,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,:demo)
              ON CONFLICT(system_id,external_id) DO UPDATE SET name=EXCLUDED.name,
              capacity=EXCLUDED.capacity, location=EXCLUDED.location, updated_at=now()
              WHERE stations.is_demo=EXCLUDED.is_demo RETURNING id"""),
                {
                    "id": identifier,
                    "system": SYSTEM,
                    "external": external,
                    "name": name,
                    "capacity": station.get("capacity"),
                    "lat": lat,
                    "lon": lon,
                    "demo": demo,
                },
            )
            persisted_id = station_result.scalar_one_or_none()
            if persisted_id is None:
                raise ValueError("Station ID already belongs to a different demo/real catalog")
            for alias in {external, str(station.get("short_name") or external)}:
                alias_result = await connection.execute(
                    text("""INSERT INTO station_aliases VALUES(:system,:alias,:id,'gbfs')
                  ON CONFLICT DO NOTHING RETURNING station_id"""),
                    {"system": SYSTEM, "alias": alias, "id": persisted_id},
                )
                if alias_result.scalar_one_or_none() is None:
                    owner = await connection.execute(
                        text(
                            "SELECT station_id FROM station_aliases "
                            "WHERE system_id=:system AND alias=:alias"
                        ),
                        {"system": SYSTEM, "alias": alias},
                    )
                    if owner.scalar_one() != persisted_id:
                        raise ValueError(
                            f"Station alias {alias} requires reviewed identity correction"
                        )
            count += 1
        return count


async def collect(document: dict[str, Any], source: str, demo: bool = False) -> dict[str, int]:
    rows, rejected = status_records(document)
    fetched = datetime.now(UTC)
    # A future report would win the latest-observation query indefinitely, hiding
    # subsequent valid feeds. Keep it in the rejection audit, not the inventory.
    future = [row for row in rows if row["reported_at"] > fetched]
    rejected += len(future)
    rows = [row for row in rows if row["reported_at"] <= fetched]
    sync_id = uuid.uuid4()
    async with engine.begin() as connection:
        # A transaction-scoped advisory lock prevents duplicate concurrent collectors.
        await connection.execute(text("SELECT pg_advisory_xact_lock(742091)"))
        await connection.execute(
            text("""INSERT INTO feed_sync_runs VALUES
          (:id,:source,:fetched,:updated,:version,:ttl,:demo,:seen,:rejected)"""),
            {
                "id": sync_id,
                "source": source,
                "fetched": fetched,
                "updated": timestamp(document["last_updated"]),
                "version": document.get("version", "1.0"),
                "ttl": document["ttl"],
                "demo": demo,
                "seen": len(rows) + rejected,
                "rejected": rejected,
            },
        )
        known: set[str] = set(
            (
                await connection.execute(
                    text(
                        "SELECT external_id FROM stations WHERE system_id=:system AND is_demo=:demo"
                    ),
                    {"system": SYSTEM, "demo": demo},
                )
            ).scalars()
        )
        inserted = 0
        mapped = 0
        for row in rows:
            if row["external_id"] not in known:
                continue
            mapped += 1
            result = await connection.execute(
                text("""INSERT INTO availability_snapshots
              SELECT id,:reported_at,:feed_updated_at,:fetched,:sync,:bikes,:docks,
              :is_installed,:is_renting,:is_returning,:demo FROM stations
              WHERE system_id=:system AND external_id=:external_id AND is_demo=:demo
              ON CONFLICT DO NOTHING"""),
                {**row, "system": SYSTEM, "demo": demo, "fetched": fetched, "sync": sync_id},
            )
            inserted += result.rowcount
        return {
            "inserted": inserted,
            "rejected": rejected,
            "unmapped": len(rows) - mapped,
            "duplicates": mapped - inserted,
        }


async def publish(stage: Stage, run_id: str) -> None:
    run = stage.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run or run["state"] != "complete":
        raise ValueError("Only complete, validated archives may be published")
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(742092)"))
        existing = (
            (
                await connection.execute(
                    text("SELECT checksum,scope,is_demo,state FROM ingestion_runs WHERE id=:id"),
                    {"id": run_id},
                )
            )
            .mappings()
            .first()
        )
        if existing:
            scope = (
                json.loads(existing["scope"])
                if isinstance(existing["scope"], str)
                else existing["scope"]
            )
            if (
                existing["checksum"] != run["checksum"]
                or scope != json.loads(run["scope"])
                or existing["is_demo"] != bool(run["demo"])
                or existing["state"] != "complete"
            ):
                raise ValueError("Conflicting completed publication identity")
            # Publication and completion are one transaction; a committed matching
            # run already owns all trip lineage and demand. No full rescan needed.
            return
        await connection.execute(
            text("""INSERT INTO ingestion_runs
          (id,source,checksum,scope,is_demo,state,rows_seen,invalid,errors)
          VALUES(:id,:source,:checksum,CAST(:scope AS jsonb),:demo,:state,:rows_seen,:invalid,
          CAST(:errors AS jsonb)) ON CONFLICT DO NOTHING"""),
            {**dict(run), "demo": bool(run["demo"])},
        )
        cursor = stage.db.execute(
            "SELECT t.* FROM trips t JOIN provenance p USING(ride_id) WHERE p.run_id=?", (run_id,)
        )
        while batch := cursor.fetchmany(2000):
            trips = [
                {
                    **dict(t),
                    "demo": bool(t["demo"]),
                    "started_at": datetime.fromisoformat(t["started_at"]),
                    "ended_at": datetime.fromisoformat(t["ended_at"]),
                }
                for t in batch
            ]
            # An archive with a reused ride ID but different content must not acquire false lineage.
            existing_rows = (
                await connection.execute(
                    text("SELECT * FROM historical_trips WHERE ride_id=ANY(CAST(:ids AS text[]))"),
                    {"ids": [trip["ride_id"] for trip in trips]},
                )
            ).mappings()
            existing_by_id = {row["ride_id"]: row for row in existing_rows}
            for trip in trips:
                existing = existing_by_id.get(trip["ride_id"])
                if existing and (
                    existing["is_demo"] != trip["demo"]
                    or any(
                        existing[key] != trip[key]
                        for key in ("started_at", "ended_at", "start_station", "end_station")
                    )
                ):
                    raise ValueError("Conflicting trip ID during publication")
            await connection.execute(
                text("""INSERT INTO historical_trips VALUES
              (:ride_id,:started_at,:ended_at,:start_station,:end_station,:demo)
              ON CONFLICT DO NOTHING"""),
                trips,
            )
            await connection.execute(
                text("INSERT INTO trip_provenance VALUES(:run,:ride) ON CONFLICT DO NOTHING"),
                [{"run": run_id, "ride": t["ride_id"]} for t in trips],
            )
        # Reuse the independently verified disk-backed aggregation instead of
        # exceeding serving query timeouts with a city-scale GROUP BY in Postgres.
        demand = stage.demand(run_id)
        while batch_demand := list(islice(demand, 2000)):
            await connection.execute(
                text("""
              INSERT INTO historical_demand(run_id,station_alias,bucket_start,departures,arrivals)
              VALUES(:run_id,:station_id,:bucket_start,:departures,:arrivals) ON CONFLICT DO NOTHING
            """),
                [
                    {**row, "bucket_start": datetime.fromisoformat(row["bucket_start"])}
                    for row in batch_demand
                ],
            )


async def nearby(
    lat: float, lon: float, radius: int, limit: int, include_demo: bool = False
) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("""SELECT s.external_id, s.name, s.capacity,
          ST_Y(s.location::geometry) latitude, ST_X(s.location::geometry) longitude,
          ST_Distance(s.location,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography)
             *1.3 walking_meters,
          a.bikes, a.docks, a.reported_at, a.feed_updated_at, a.fetched_at,
          a.is_installed, a.is_renting, a.is_returning, s.is_demo,
          (a.reported_at IS NULL OR LEAST(a.reported_at,a.feed_updated_at,a.fetched_at)
             <now()-interval '5 minutes'
            OR GREATEST(a.reported_at,a.feed_updated_at,a.fetched_at)>now()) stale
          FROM stations s LEFT JOIN LATERAL (SELECT * FROM availability_snapshots
             WHERE station_id=s.id AND is_demo=s.is_demo
             ORDER BY reported_at DESC LIMIT 1) a ON true
          WHERE s.system_id=:system AND s.is_active AND (:demo OR NOT s.is_demo)
          AND ST_DWithin(s.location,ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography,:radius)
          ORDER BY walking_meters,s.external_id LIMIT :limit"""),
            {
                "system": SYSTEM,
                "lat": lat,
                "lon": lon,
                "radius": radius,
                "limit": limit,
                "demo": include_demo or get_settings().demo_mode,
            },
        )
        return [dict(row) for row in result.mappings()]
