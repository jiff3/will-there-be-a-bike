"""Recover past-known coordinates, not today's GBFS capacity, from official trip rows."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.modules.ingestion.history import bucket, local_time, normalize
from app.modules.ml.data import checksum


def extract_locations(archive: Path, catalog_path: Path, before: str) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    aliases = {
        str(s.get("short_name") or s["station_id"]): str(s["station_id"])
        for s in catalog["data"]["stations"]
    }
    selected = set(aliases.values())
    cutoff = local_time(before)
    found: dict[str, dict[str, Any]] = {}
    scanned = 0
    with zipfile.ZipFile(archive) as zipped:
        for name in sorted(zipped.namelist()):
            if not name.endswith(".csv") or name.startswith("__MACOSX/"):
                continue
            with zipped.open(name) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                    scanned += 1
                    if not any(
                        aliases.get(row.get(f"{side}_station_id", "")) in selected
                        for side in ("start", "end")
                    ):
                        continue
                    try:
                        trip = normalize(row, aliases)
                    except (ValueError, TypeError, KeyError, OverflowError):
                        continue
                    for side, field in (("start", "started_at"), ("end", "ended_at")):
                        station = trip[f"{side}_station"]
                        # Coordinate known only once this complete valid trip has ended.
                        known = local_time(trip["ended_at"])
                        if station not in selected or known >= cutoff:
                            continue
                        rounded = local_time(bucket(known.isoformat())) + timedelta(minutes=15)
                        if (
                            station not in found
                            or rounded.isoformat() < found[station]["observed_at"]
                        ):
                            found[station] = {
                                "lat": float(row[f"{side}_lat"]),
                                "lon": float(row[f"{side}_lng"]),
                                "observed_at": rounded.isoformat(),
                                "capacity": None,
                                "ride_id": trip["ride_id"],
                                "event_at": trip[field],
                            }
    return {
        "schema_version": 1,
        "archive": archive.name,
        "sha256": checksum(archive),
        "before": cutoff.isoformat(),
        "rows_scanned": scanned,
        "stations": found,
        "semantics": "Past observed reference coordinates; NOT historical capacity. "
        "Publication time is unknown; offline event-time replay only.",
    }
