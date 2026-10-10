"""Inventory known at an issuance boundary; model service owns freshness decisions."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ApplicationError
from app.modules.ingestion.persistence import SYSTEM
from app.persistence.database import engine


async def measured_inventory(station_id: str, at: datetime) -> dict[str, Any] | None:
    async def query() -> dict[str, Any] | None:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("""
                SELECT s.external_id station_id, x.bikes, x.docks, x.reported_at,
                  x.feed_updated_at, x.fetched_at, x.is_installed, x.is_renting,
                  x.is_returning, x.is_demo
                FROM stations s JOIN availability_snapshots x ON x.station_id=s.id
                WHERE s.system_id=:system AND s.external_id=:station
                  AND NOT s.is_demo AND NOT x.is_demo
                  AND x.reported_at<=:at AND x.feed_updated_at<=:at AND x.fetched_at<=:at
                ORDER BY x.reported_at DESC LIMIT 1
                """),
                {"system": SYSTEM, "station": station_id, "at": at},
            )
            row = result.mappings().first()
            if row is None:
                return None
            return {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in dict(row).items()
            }

    try:
        return await asyncio.wait_for(query(), timeout=2)
    except (SQLAlchemyError, OSError, asyncio.TimeoutError) as error:  # noqa: UP041
        raise ApplicationError(
            "inventory_unavailable", "Inventory database unavailable", 503
        ) from error
