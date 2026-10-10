from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.modules.replay import service

router = APIRouter()
ReplayDay = Annotated[date, Query(le=date(9999, 12, 30))]


@router.get("/catalog")
async def catalog() -> dict[str, Any]:
    return await service.catalog()


@router.get("/day")
async def day(day: ReplayDay) -> dict[str, Any]:
    return await service.day_data(day)


@router.get("/stations/{station}/comparisons")
async def comparison(station: str, day: ReplayDay) -> dict[str, Any]:
    return await service.comparisons(station, day)
