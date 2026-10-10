from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc  # noqa: UP017 -- local Python 3.10 verification


def timestamp(value: str | int | float) -> datetime:
    if isinstance(value, bool):
        raise ValueError("GBFS timestamp cannot be boolean")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("GBFS timestamp must include timezone")
    return parsed.astimezone(UTC)


def stale(
    reported: datetime, updated: datetime, fetched: datetime, now: datetime, threshold: int = 300
) -> bool:
    return any(
        (now - value).total_seconds() > threshold or value > now
        for value in (reported, updated, fetched)
    )


def walking_distance(lat: float, lon: float, other_lat: float, other_lon: float) -> float:
    # Great-circle length * detour factor; not a routed pedestrian distance.
    a, b = math.radians(lat), math.radians(other_lat)
    dlat, dlon = b - a, math.radians(other_lon - lon)
    hav = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, hav))) * 1.3


def status_records(document: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    version = document.get("version", "1.0")
    if version.split(".")[0] not in {"1", "2", "3"}:
        raise ValueError(f"Unsupported GBFS version: {version}")
    updated = timestamp(document["last_updated"])
    if type(document["ttl"]) is not int or document["ttl"] < 0:
        raise ValueError("GBFS TTL must be a nonnegative integer")
    records, rejected = [], 0
    for row in document["data"]["stations"]:
        try:
            if not isinstance(row["station_id"], str) or not row["station_id"].strip():
                raise ValueError("Invalid station identifier")
            bikes = row.get("num_vehicles_available", row.get("num_bikes_available"))
            docks = row["num_docks_available"]
            if type(bikes) is not int or type(docks) is not int or bikes < 0 or docks < 0:
                raise ValueError("Invalid inventory")
            flags = [row[field] for field in ("is_installed", "is_renting", "is_returning")]
            if any(type(value) not in (bool, int) or value not in (0, 1) for value in flags):
                raise ValueError("Invalid operational flag")
            reported = timestamp(row["last_reported"])
            if reported > updated:
                raise ValueError("Station report cannot postdate its feed")
            records.append(
                {
                    "external_id": str(row["station_id"]),
                    "bikes": bikes,
                    "docks": docks,
                    "reported_at": reported,
                    "feed_updated_at": updated,
                    "is_installed": bool(flags[0]),
                    "is_renting": bool(flags[1]),
                    "is_returning": bool(flags[2]),
                }
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected += 1
    return records, rejected
