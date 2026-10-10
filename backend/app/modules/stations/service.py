"""Canonical catalog and indexed PostGIS queries with latest measured observations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.modules.ingestion.persistence import SYSTEM
from app.persistence.database import engine

SELECT = """
 SELECT s.external_id,s.name,s.capacity,s.is_demo,s.is_active,s.updated_at catalog_updated_at,
 ST_Y(s.location::geometry) latitude,ST_X(s.location::geometry) longitude,
 a.bikes,a.docks,a.reported_at,a.feed_updated_at,a.fetched_at,
 a.is_installed,a.is_renting,a.is_returning,
 (a.reported_at IS NULL OR
  LEAST(a.reported_at,a.feed_updated_at,a.fetched_at)<now()-interval '5 minutes'
  OR GREATEST(a.reported_at,a.feed_updated_at,a.fetched_at)>now()) stale
 FROM stations s LEFT JOIN LATERAL (
   SELECT * FROM availability_snapshots WHERE station_id=s.id AND is_demo=s.is_demo
   ORDER BY reported_at DESC LIMIT 1
 ) a ON true
"""


async def catalog(
    limit: int = 20,
    offset: int = 0,
    search: str = "",
    bbox: tuple[float, ...] | None = None,
    station_id: str | None = None,
    available: str | None = None,
    include_demo: bool = False,
) -> dict[str, Any]:
    where = "WHERE s.system_id=:system AND s.is_active AND (:demo OR NOT s.is_demo)"
    params: dict[str, Any] = {
        "system": SYSTEM,
        "demo": include_demo or get_settings().demo_mode,
        "limit": limit + 1,
        "offset": offset,
    }
    if station_id is not None:
        where += " AND s.external_id=:station"
        params["station"] = station_id
    if search:
        where += " AND s.name ILIKE :search ESCAPE '\\'"
        params["search"] = (
            "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
    if bbox:
        where += " AND s.location && ST_MakeEnvelope(:west,:south,:east,:north,4326)::geography"
        params.update(dict(zip(("west", "south", "east", "north"), bbox, strict=True)))
    if available:
        field = "bikes" if available == "bikes" else "docks"
        flag = "is_renting" if available == "bikes" else "is_returning"
        where += f" AND a.{field}>0 AND a.is_installed AND a.{flag} AND NOT s.is_demo AND " + (
            "LEAST(a.reported_at,a.feed_updated_at,a.fetched_at)>=now()-interval '5 minutes' "
            "AND GREATEST(a.reported_at,a.feed_updated_at,a.fetched_at)<=now()"
        )
    async with engine.connect() as connection:
        result = await connection.execute(
            text(SELECT + where + " ORDER BY s.external_id LIMIT :limit OFFSET :offset"), params
        )
        rows = [dict(row) for row in result.mappings()]
    for row in rows:
        row["freshness"] = (
            "missing" if row["reported_at"] is None else "stale" if row["stale"] else "fresh"
        )
        row["distance_method"] = "great_circle_times_1.3_not_pedestrian_routing"
    return {
        "items": rows[:limit],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if len(rows) > limit else None,
    }


async def station(station_id: str) -> dict[str, Any]:
    result = await catalog(station_id=station_id, limit=1)
    if not result["items"]:
        raise ApplicationError("station_not_found", "Station not found", 404)
    return dict(result["items"][0])
