from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.modules.ingestion.persistence import nearby

router = APIRouter()


class NearbyStation(BaseModel):
    distance_method: str = "great_circle_times_1.3_not_pedestrian_routing"
    external_id: str
    name: str
    capacity: int | None
    latitude: float
    longitude: float
    walking_meters: float = Field(ge=0)
    bikes: int | None = Field(ge=0)
    docks: int | None = Field(ge=0)
    reported_at: datetime | None
    feed_updated_at: datetime | None
    fetched_at: datetime | None
    is_installed: bool | None
    is_renting: bool | None
    is_returning: bool | None
    is_demo: bool
    stale: bool


@router.get("/nearby", response_model=list[NearbyStation])
async def nearby_stations(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    radius: Annotated[int, Query(ge=50, le=5000)] = 1000,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    include_demo: bool = False,
) -> list[NearbyStation]:
    return [
        NearbyStation.model_validate(row)
        for row in await nearby(lat, lon, radius, limit, include_demo)
    ]
