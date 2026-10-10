from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query
from pydantic import AwareDatetime

from app.core.errors import ApplicationError
from app.modules.forecasts.schemas import ForecastResponse
from app.modules.stations.schemas import StationPage, StationResponse
from app.modules.stations.service import catalog, station

router = APIRouter()
StationId = Annotated[str, Path(min_length=1, max_length=128)]


@router.get("", response_model=StationPage)
async def list_stations(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    search: Annotated[str, Query(max_length=100)] = "",
    bbox: Annotated[
        str | None, Query(description="west,south,east,north; no antimeridian wrapping")
    ] = None,
    available: Literal["bikes", "docks"] | None = None,
    include_demo: bool = False,
) -> dict[str, Any]:
    bounds = None
    if bbox:
        try:
            bounds = tuple(float(value) for value in bbox.split(","))
            if len(bounds) != 4 or not (
                -180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90
            ):
                raise ValueError
        except ValueError as error:
            raise ApplicationError(
                "invalid_bbox", "bbox must be west,south,east,north within WGS84 bounds", 422
            ) from error
    return await catalog(
        limit, offset, search, bounds, available=available, include_demo=include_demo
    )


@router.get("/{station_id}", response_model=StationResponse)
async def station_metadata(station_id: StationId) -> dict[str, Any]:
    return await station(station_id)


@router.get("/{station_id}/availability", response_model=StationResponse)
async def availability(station_id: StationId) -> dict[str, Any]:
    return await station(station_id)


@router.get("/{station_id}/predictions", response_model=ForecastResponse)
async def predictions(station_id: StationId) -> dict[str, Any]:
    from app.modules.forecasts.service import current_forecast

    await station(station_id)
    return await current_forecast(station_id)


@router.get("/{station_id}/demand")
async def demand(station_id: StationId, start: AwareDatetime, end: AwareDatetime) -> dict[str, Any]:
    from app.modules.stations.history import observed_demand

    if (
        start.timestamp() % 900
        or end.timestamp() % 900
        or not 0 < (end - start).total_seconds() <= 7 * 86400
    ):
        raise ApplicationError(
            "invalid_range",
            "Use aligned 15-minute boundaries with a range of at most seven days",
            422,
        )
    await station(station_id)
    return await observed_demand(station_id, start, end)
