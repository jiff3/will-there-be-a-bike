"""Bounded, coverage-aware demand input. Missing observations are never zero-filled."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[Any]
UTC = timezone.utc  # noqa: UP017 -- native Windows QA also supports Python 3.10
STEP = 900


def epoch(value: str) -> int:
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if at.tzinfo is None or at.second or at.microsecond or at.minute % 15:
        raise ValueError("Demand timestamps require timezone and 15-minute alignment")
    return int(at.timestamp())


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Demand:
    stations: list[str]
    times: Array
    counts: Array  # station, UTC time, [departures, arrivals]; float32 NaN = unknown
    source_sha256: str
    is_demo: bool = False

    def index(self, at: int) -> int:
        offset = at - int(self.times[0])
        if offset % STEP:
            raise ValueError("Unaligned prediction time")
        return offset // STEP


def load_demand(path: Path, allow_demo: bool = False, max_cells: int = 1_200_000) -> Demand:
    """Two streaming passes; never retain the JSON records in memory."""
    if path.stat().st_size > 512_000_000:
        raise ValueError("Demand export exceeds 512 MB budget; use a smaller station/date scope")
    stations: set[str] = set()
    first, last = 2**63 - 1, 0
    rows, modes = 0, set()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            station = row["station_id"]
            if not isinstance(station, str) or not station:
                raise ValueError("Station identity must be a nonempty string")
            stations.add(station)
            at = epoch(row["bucket_start"])
            first, last = min(first, at), max(last, at)
            if type(row["is_demo"]) is not bool:
                raise ValueError("is_demo must be boolean")
            modes.add(row["is_demo"])
            rows += 1
            if rows > max_cells or len(stations) > 120:
                raise ValueError("Training budget: at most 120 stations / 1.2 million cells")
    if not rows or len(modes) != 1 or (True in modes and not allow_demo):
        raise ValueError("Empty, mixed or unapproved synthetic dataset")
    size = (last - first) // STEP + 1
    if size * len(stations) > max_cells:
        raise ValueError("Dense grid exceeds training budget")
    ordered = sorted(stations)
    lookup = {station: index for index, station in enumerate(ordered)}
    counts = np.full((len(ordered), size, 2), np.nan, dtype=np.float32)
    seen = np.zeros((len(ordered), size), dtype=np.bool_)
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            s, t = lookup[row["station_id"]], (epoch(row["bucket_start"]) - first) // STEP
            if seen[s, t]:
                raise ValueError("Duplicate station/time observation")
            seen[s, t] = True
            for d, (field, mask) in enumerate(
                (("departures", "departure_coverage"), ("arrivals", "arrival_coverage"))
            ):
                if type(row[mask]) is not bool:
                    raise ValueError("Coverage must be boolean")
                value = row[field]
                if value is not None and (type(value) is not int or not 0 <= value <= 10000):
                    raise ValueError("Counts must be bounded nonnegative integers")
                if row[mask]:
                    if value is None:
                        raise ValueError("Covered observations cannot be null")
                    counts[s, t, d] = value
    return Demand(
        ordered,
        np.arange(first, last + STEP, STEP, dtype=np.int64),
        counts,
        checksum(path),
        True in modes,
    )


def load_locations(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("Unsupported point-in-time location schema")
    result = document["stations"]
    for station, row in result.items():
        if (
            not isinstance(station, str)
            or not -90 <= row["lat"] <= 90
            or not -180 <= row["lon"] <= 180
        ):
            raise ValueError("Invalid location")
        epoch(row["observed_at"])
        if row.get("capacity") is not None and (
            type(row["capacity"]) is not int or not 1 <= row["capacity"] <= 1000
        ):
            raise ValueError("Invalid reliable capacity")
    return dict(result)
