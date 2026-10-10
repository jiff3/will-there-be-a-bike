"""Ownership is part of every rider query, never supplied in a request body."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Insert, Update, delete, insert, select, text, update

from app.core.errors import ApplicationError
from app.modules.ingestion.persistence import SYSTEM
from app.persistence.database import engine
from app.persistence.rider_tables import commutes, notifications, rules


def missing() -> ApplicationError:
    return ApplicationError("not_found", "Saved item not found", 404)


async def list_commutes(owner: uuid.UUID) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        return [
            dict(row)
            for row in (
                await connection.execute(
                    select(commutes)
                    .where(commutes.c.user_id == owner)
                    .order_by(commutes.c.created_at)
                )
            ).mappings()
        ]


async def get_commute(owner: uuid.UUID, identifier: uuid.UUID) -> dict[str, Any]:
    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(commutes).where(commutes.c.id == identifier, commutes.c.user_id == owner)
                )
            )
            .mappings()
            .first()
        )
    if not row:
        raise missing()
    return dict(row)


async def save_commute(
    owner: uuid.UUID, config: dict[str, Any], identifier: uuid.UUID | None = None
) -> dict[str, Any]:
    statement: Insert | Update
    async with engine.begin() as connection:
        # Serialize account quotas and child writes against deletion.
        await connection.execute(
            text("SELECT id FROM users WHERE id=:owner FOR UPDATE"), {"owner": owner}
        )
        if identifier:
            statement = (
                update(commutes)
                .where(commutes.c.id == identifier, commutes.c.user_id == owner)
                .values(config=config, updated_at=datetime.now().astimezone())
            )
        else:
            count = await connection.scalar(
                text("SELECT count(*) FROM saved_commutes WHERE user_id=:owner"), {"owner": owner}
            )
            if int(count or 0) >= 20:
                raise ApplicationError("commute_limit", "You can save up to 20 commutes", 409)
            statement = insert(commutes).values(id=uuid.uuid4(), user_id=owner, config=config)
        row = (await connection.execute(statement.returning(commutes))).mappings().first()
        if not row:
            raise missing()
        return dict(row)


async def remove_commute(owner: uuid.UUID, identifier: uuid.UUID) -> None:
    async with engine.begin() as connection:
        row = await connection.scalar(
            delete(commutes)
            .where(commutes.c.id == identifier, commutes.c.user_id == owner)
            .returning(commutes.c.id)
        )
        if not row:
            raise missing()


async def list_alerts(owner: uuid.UUID, commute: uuid.UUID) -> list[dict[str, Any]]:
    await get_commute(owner, commute)
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("""
          SELECT a.*,s.external_id,s.name station_name,
            ST_Y(s.location::geometry) latitude,ST_X(s.location::geometry) longitude
          FROM availability_alerts a
          JOIN saved_commutes c ON c.id=a.commute_id JOIN stations s ON s.id=a.station_id
          WHERE c.user_id=:owner AND c.id=:commute ORDER BY a.id
        """),
            {"owner": owner, "commute": commute},
        )
        return [{**dict(row), "station_id": row["external_id"]} for row in rows.mappings()]


async def save_alert(
    owner: uuid.UUID, commute: uuid.UUID, values: dict[str, Any], identifier: uuid.UUID | None
) -> uuid.UUID:
    statement: Insert | Update
    values = dict(values)
    async with engine.begin() as connection:
        parent = await connection.scalar(
            select(commutes.c.id)
            .where(commutes.c.id == commute, commutes.c.user_id == owner)
            .with_for_update()
        )
        if not parent:
            raise missing()
        station = await connection.scalar(
            text(
                "SELECT id FROM stations WHERE external_id=:station "
                "AND system_id=:system AND NOT is_demo AND is_active"
            ),
            {"station": values["station_id"], "system": SYSTEM},
        )
        if not station:
            raise ApplicationError("station_not_found", "Choose an active real station", 404)
        values["station_id"] = station
        if identifier:
            statement = (
                update(rules)
                .where(rules.c.id == identifier, rules.c.commute_id == commute)
                .values(**values, last_checked_at=None, last_status="not_checked")
            )
        else:
            count = await connection.scalar(
                text("SELECT count(*) FROM availability_alerts WHERE commute_id=:commute"),
                {"commute": commute},
            )
            if int(count or 0) >= 4:
                raise ApplicationError("alert_limit", "Use up to four alerts per commute", 409)
            identifier = uuid.uuid4()
            statement = insert(rules).values(id=identifier, commute_id=commute, **values)
        if not await connection.scalar(statement.returning(rules.c.id)):
            raise missing()
    return identifier


async def remove_alert(owner: uuid.UUID, commute: uuid.UUID, identifier: uuid.UUID) -> None:
    async with engine.begin() as connection:
        parent = select(commutes.c.id).where(commutes.c.user_id == owner, commutes.c.id == commute)
        row = await connection.scalar(
            delete(rules)
            .where(rules.c.id == identifier, rules.c.commute_id.in_(parent))
            .returning(rules.c.id)
        )
        if not row:
            raise missing()


async def inbox(owner: uuid.UUID, limit: int, offset: int) -> dict[str, Any]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                select(notifications)
                .where(notifications.c.user_id == owner)
                .order_by(notifications.c.created_at.desc(), notifications.c.id)
                .limit(limit + 1)
                .offset(offset)
            )
        ).mappings()
        items = [dict(row) for row in rows]
        unread = await connection.scalar(
            text("SELECT count(*) FROM notifications WHERE user_id=:owner AND read_at IS NULL"),
            {"owner": owner},
        )
    return {
        "items": items[:limit],
        "unread_count": unread,
        "next_offset": offset + limit if len(items) > limit else None,
    }


async def set_read(owner: uuid.UUID, identifier: uuid.UUID, read: bool) -> None:
    async with engine.begin() as connection:
        row = await connection.scalar(
            update(notifications)
            .where(notifications.c.id == identifier, notifications.c.user_id == owner)
            .values(read_at=datetime.now().astimezone() if read else None)
            .returning(notifications.c.id)
        )
        if not row:
            raise missing()
