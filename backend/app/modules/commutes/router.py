from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from app.modules.auth.router import User
from app.modules.commutes.schemas import AlertInput, CommuteInput, ReadState, next_departure
from app.modules.recommendations.router import TripRequest, recommend
from app.persistence import rider_repository as repository

router = APIRouter()


def present(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        **row["config"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "next_departure": next_departure(row["config"], datetime.now(UTC)),
    }


@router.get("/commutes")
async def commutes(user: User) -> dict[str, Any]:
    return {"items": [present(row) for row in await repository.list_commutes(user["id"])]}


@router.post("/commutes", status_code=201)
async def create(body: CommuteInput, user: User) -> dict[str, Any]:
    return present(await repository.save_commute(user["id"], body.model_dump()))


@router.get("/commutes/{identifier}")
async def get(identifier: uuid.UUID, user: User) -> dict[str, Any]:
    return present(await repository.get_commute(user["id"], identifier))


@router.put("/commutes/{identifier}")
async def edit(identifier: uuid.UUID, body: CommuteInput, user: User) -> dict[str, Any]:
    return present(await repository.save_commute(user["id"], body.model_dump(), identifier))


@router.delete("/commutes/{identifier}", status_code=204)
async def remove(identifier: uuid.UUID, user: User) -> None:
    await repository.remove_commute(user["id"], identifier)


@router.get("/commutes/{identifier}/recommendations")
async def current_recommendations(identifier: uuid.UUID, user: User) -> dict[str, Any]:
    row = await repository.get_commute(user["id"], identifier)
    config = row["config"]
    # An immediate check, not a fictitious forecast for a commute days away.
    body = TripRequest(
        origin={key: config["origin"][key] for key in ("latitude", "longitude")},
        destination={key: config["destination"][key] for key in ("latitude", "longitude")},
        departure_time=datetime.now(UTC),
        preferences=config["preferences"],
        limit=3,
    )
    return await recommend(body)


@router.get("/commutes/{commute}/alerts")
async def alerts(commute: uuid.UUID, user: User) -> dict[str, Any]:
    return {"items": await repository.list_alerts(user["id"], commute)}


@router.post("/commutes/{commute}/alerts", status_code=201)
async def create_alert(commute: uuid.UUID, body: AlertInput, user: User) -> dict[str, Any]:
    identifier = await repository.save_alert(user["id"], commute, body.model_dump(), None)
    return next(
        row for row in await repository.list_alerts(user["id"], commute) if row["id"] == identifier
    )


@router.put("/commutes/{commute}/alerts/{identifier}")
async def edit_alert(
    commute: uuid.UUID, identifier: uuid.UUID, body: AlertInput, user: User
) -> dict[str, Any]:
    await repository.save_alert(user["id"], commute, body.model_dump(), identifier)
    return next(
        row for row in await repository.list_alerts(user["id"], commute) if row["id"] == identifier
    )


@router.delete("/commutes/{commute}/alerts/{identifier}", status_code=204)
async def delete_alert(commute: uuid.UUID, identifier: uuid.UUID, user: User) -> None:
    await repository.remove_alert(user["id"], commute, identifier)


@router.get("/notifications")
async def notifications(
    user: User, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=10000)
) -> dict[str, Any]:
    return await repository.inbox(user["id"], limit, offset)


@router.patch("/notifications/{identifier}", status_code=204)
async def mark_read(identifier: uuid.UUID, body: ReadState, user: User) -> None:
    await repository.set_read(user["id"], identifier, body.read)
