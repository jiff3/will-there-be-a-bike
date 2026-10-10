from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.auth.router import Preferences
from app.modules.recommendations.router import Point


class Place(Point):
    label: str = Field(min_length=1, max_length=120)
    station_id: str | None = Field(default=None, max_length=120)


class CommuteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    origin: Place
    destination: Place
    departure_local: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "America/New_York"
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5], min_length=1, max_length=7)
    preferences: Preferences = Field(default_factory=Preferences)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Use a supported IANA time zone") from error
        return value

    @field_validator("weekdays")
    @classmethod
    def valid_days(cls, value: list[int]) -> list[int]:
        if any(day not in range(1, 8) for day in value) or len(set(value)) != len(value):
            raise ValueError("Use unique ISO weekdays, Monday=1 through Sunday=7")
        return sorted(value)


class AlertInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: str = Field(min_length=1, max_length=120)
    kind: Literal["pickup_empty", "destination_full"]
    mode: Literal["inventory", "estimated_risk"] = "inventory"
    threshold: float = Field(default=3.0, ge=0, le=100, allow_inf_nan=False)
    lead_minutes: Literal[30, 60] = 30
    cooldown_minutes: int = Field(default=60, ge=15, le=1440)
    enabled: bool = True

    @model_validator(mode="after")
    def threshold_units(self) -> AlertInput:
        if self.mode == "inventory" and not self.threshold.is_integer():
            raise ValueError("Inventory threshold must be a whole number of bikes/docks")
        return self


class ReadState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read: bool


def next_departure(config: dict[str, object], now: datetime) -> datetime:
    """Skip nonexistent DST times; ambiguous times use the first occurrence only."""
    from datetime import timedelta

    zone = ZoneInfo(str(config["timezone"]))
    local = now.astimezone(zone)
    hour, minute = map(int, str(config["departure_local"]).split(":"))
    for offset in range(9):
        date = local.date() + timedelta(days=offset)
        if date.isoweekday() not in config["weekdays"]:  # type: ignore[operator]
            continue
        naive = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)
        candidate = naive.replace(tzinfo=zone, fold=0).astimezone(now.tzinfo)
        if candidate.astimezone(zone).replace(tzinfo=None) != naive:
            continue
        if candidate >= now:
            return candidate
    raise ValueError("No supported departure in the next week")
