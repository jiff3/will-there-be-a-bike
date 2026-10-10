from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class StationResponse(BaseModel):
    external_id: str
    name: str
    capacity: int | None
    is_demo: bool
    is_active: bool
    catalog_updated_at: datetime
    latitude: float
    longitude: float
    bikes: int | None
    docks: int | None
    reported_at: datetime | None
    feed_updated_at: datetime | None
    fetched_at: datetime | None
    is_installed: bool | None
    is_renting: bool | None
    is_returning: bool | None
    stale: bool
    freshness: Literal["fresh", "stale", "missing"]
    distance_method: str


class StationPage(BaseModel):
    items: list[StationResponse]
    limit: int
    offset: int
    next_offset: int | None
