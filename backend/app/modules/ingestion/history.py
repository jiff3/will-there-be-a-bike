"""Streaming trip normalization and resumable disk staging; no inventory inference."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import sqlite3
import zipfile
from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = timezone.utc  # noqa: UP017 -- local Python 3.10 verification
REQUIRED = {
    "ride_id",
    "started_at",
    "ended_at",
    "start_station_id",
    "end_station_id",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
}
LEGACY = {
    "starttime": "started_at",
    "stoptime": "ended_at",
    "start station id": "start_station_id",
    "end station id": "end_station_id",
    "start station latitude": "start_lat",
    "start station longitude": "start_lng",
    "end station latitude": "end_lat",
    "end station longitude": "end_lng",
}


@lru_cache(maxsize=512)
def regular_day(day: date) -> bool:
    midnight = datetime.combine(day, time(), NY)
    return midnight.utcoffset() == (midnight + timedelta(days=1)).utcoffset()


def local_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo:
        return parsed.astimezone(UTC)
    # NY changes offsets only on transition days. Cache the safe days, not trip timestamps.
    if regular_day(parsed.date()):
        return parsed.replace(tzinfo=NY).astimezone(UTC)
    candidates = {
        parsed.replace(tzinfo=NY, fold=fold).astimezone(UTC)
        for fold in (0, 1)
        if parsed.replace(tzinfo=NY, fold=fold).astimezone(UTC).astimezone(NY).replace(tzinfo=None)
        == parsed
    }
    if len(candidates) != 1:
        raise ValueError("ambiguous_or_nonexistent_local_time")
    return candidates.pop()


def bucket(value: str) -> str:
    date = datetime.fromisoformat(value)
    return date.replace(minute=date.minute // 15 * 15, second=0, microsecond=0).isoformat()


def normalize(row: dict[str, str], aliases: dict[str, str]) -> dict[str, Any]:
    row = {LEGACY.get(key, key): value for key, value in row.items()}
    start, end = local_time(row["started_at"]), local_time(row["ended_at"])
    if not 60 <= (end - start).total_seconds() <= 86400:
        raise ValueError("invalid_duration")
    stations = []
    for side in ("start", "end"):
        raw_station = row[f"{side}_station_id"]
        if not isinstance(raw_station, str) or not raw_station.strip():
            raise ValueError("missing_station_id")
        station = raw_station.strip()
        # IDs are strings: decimal identifiers must not be cast to floats.
        stations.append(aliases.get(station, station))
        lat, lon = float(row[f"{side}_lat"]), float(row[f"{side}_lng"])
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == lon == 0):
            raise ValueError("invalid_coordinates")
    ride_id = (row.get("ride_id") or "").strip()
    if not ride_id:
        # Legacy exports have no ride ID; deterministic full-row fingerprint.
        ride_id = "legacy:" + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return {
        "ride_id": ride_id,
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "start_station": stations[0],
        "end_station": stations[1],
    }


class Stage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA wal_autocheckpoint=10000;
        PRAGMA cache_size=-262144;
        PRAGMA temp_store=FILE;
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, source TEXT, checksum TEXT, scope TEXT, demo INTEGER,
          state TEXT, rows_seen INTEGER DEFAULT 0, invalid INTEGER DEFAULT 0,
          errors TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS checkpoints (
          run_id TEXT, member TEXT, row_number INTEGER, PRIMARY KEY(run_id,member));
        CREATE TABLE IF NOT EXISTS trips (
          ride_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT,
          start_station TEXT, end_station TEXT, demo INTEGER);
        CREATE TABLE IF NOT EXISTS provenance (
          run_id TEXT, ride_id TEXT, PRIMARY KEY(run_id,ride_id));
        CREATE INDEX IF NOT EXISTS ix_trips_start ON trips(start_station,started_at);
        CREATE INDEX IF NOT EXISTS ix_trips_end ON trips(end_station,ended_at);
        CREATE TABLE IF NOT EXISTS live_runs (
          id TEXT PRIMARY KEY, source TEXT, fetched_at TEXT, feed_updated_at TEXT,
          state TEXT, is_demo INTEGER, rejected INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS live_snapshots (
          station_id TEXT, reported_at TEXT, is_demo INTEGER, sync_id TEXT,
          payload TEXT, PRIMARY KEY(station_id,reported_at,is_demo));
        """)

    def ingest(
        self,
        archive: Path,
        start: str,
        end: str,
        stations: set[str],
        aliases: dict[str, str] | None = None,
        chunk_size: int = 5000,
        demo: bool = False,
        max_rows: int | None = None,
        source: str | None = None,
    ) -> str:
        if chunk_size < 1 or not stations or len(stations) > 5000:
            raise ValueError("Invalid chunk size or station scope")
        lower, upper = local_time(start), local_time(end)
        if lower >= upper:
            raise ValueError("End must follow start (end is exclusive)")
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        scope = json.dumps(
            {"start": start, "end": end, "stations": sorted(stations), "aliases": aliases or {}},
            sort_keys=True,
        )
        run_id = hashlib.sha256((digest.hexdigest() + scope + str(demo)).encode()).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO runs(id,source,checksum,scope,demo,state) "
            "VALUES(?,?,?,?,?,'running')",
            (run_id, source or archive.name, digest.hexdigest(), scope, demo),
        )
        self.db.commit()
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if run["state"] == "complete":
            return run_id
        errors = Counter(json.loads(run["errors"]))
        seen, invalid, processed = run["rows_seen"], run["invalid"], 0
        with zipfile.ZipFile(archive) as zipped:
            members = sorted(
                m for m in zipped.namelist() if m.endswith(".csv") and not m.startswith("__MACOSX/")
            )
            if not members:
                raise ValueError("Archive has no CSV members")
            for member in members:
                checkpoint = self.db.execute(
                    "SELECT row_number FROM checkpoints WHERE run_id=? AND member=?",
                    (run_id, member),
                ).fetchone()
                skip = checkpoint[0] if checkpoint else 0
                with zipped.open(member) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                    fields = {LEGACY.get(f, f) for f in reader.fieldnames or []}
                    if not (REQUIRED - {"ride_id"}) <= fields:
                        raise ValueError(f"Unsupported schema in {member}: {sorted(fields)}")
                    if "ride_id" not in fields and "starttime" not in (reader.fieldnames or []):
                        raise ValueError("Modern schema requires ride_id")
                    number = 0
                    for number, row in enumerate(reader, 1):
                        if number <= skip:
                            continue
                        seen += 1
                        processed += 1
                        try:
                            trip = normalize(row, aliases or {})
                            if not (row.get("ride_id") or "").strip() and "starttime" not in row:
                                raise ValueError("missing_ride_id")
                            selected = (
                                trip["start_station"] in stations or trip["end_station"] in stations
                            )
                            if (
                                lower <= datetime.fromisoformat(trip["started_at"]) < upper
                                and selected
                            ):
                                values = tuple(trip.values()) + (demo,)
                                inserted = self.db.execute(
                                    "INSERT OR IGNORE INTO trips VALUES(?,?,?,?,?,?)", values
                                )
                                if inserted.rowcount == 0:
                                    existing = self.db.execute(
                                        "SELECT * FROM trips WHERE ride_id=?", (trip["ride_id"],)
                                    ).fetchone()
                                    if existing and tuple(existing) != values:
                                        raise ValueError("conflicting_duplicate")
                                self.db.execute(
                                    "INSERT OR IGNORE INTO provenance VALUES(?,?)",
                                    (run_id, trip["ride_id"]),
                                )
                        except (ValueError, TypeError, KeyError, OverflowError) as error:
                            invalid += 1
                            code = str(error)
                            errors[
                                code
                                if code
                                in {
                                    "ambiguous_or_nonexistent_local_time",
                                    "invalid_duration",
                                    "missing_station_id",
                                    "invalid_coordinates",
                                    "missing_ride_id",
                                    "conflicting_duplicate",
                                }
                                else "invalid_record"
                            ] += 1
                        if number % chunk_size == 0 or (max_rows and processed >= max_rows):
                            self._checkpoint(run_id, member, number, seen, invalid, errors)
                        if max_rows and processed >= max_rows:
                            return run_id  # Deliberately partial; never declares zero coverage.
                    self._checkpoint(run_id, member, number, seen, invalid, errors)
        if seen == 0 or invalid == seen:
            self.db.execute("UPDATE runs SET state='rejected' WHERE id=?", (run_id,))
            self.db.commit()
            raise ValueError("Archive has no valid source observations; coverage is unverified")
        self.db.execute("UPDATE runs SET state='complete' WHERE id=?", (run_id,))
        self.db.commit()
        return run_id

    def _checkpoint(
        self, run: str, member: str, number: int, seen: int, invalid: int, errors: Counter[str]
    ) -> None:
        if shutil.disk_usage(self.path.parent).free < 1_000_000_000:
            self.db.rollback()
            raise ValueError("Insufficient staging disk headroom; last checkpoint preserved")
        self.db.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)", (run, member, number))
        self.db.execute(
            "UPDATE runs SET rows_seen=?, invalid=?, errors=? WHERE id=?",
            (seen, invalid, json.dumps(errors), run),
        )
        self.db.commit()  # Trips, provenance and checkpoint are atomic.

    def report(self) -> dict[str, Any]:
        return {
            "runs": [dict(r) for r in self.db.execute("SELECT * FROM runs")],
            "unique_trips": self.db.execute("SELECT count(*) FROM trips").fetchone()[0],
            "live_snapshots": self.db.execute("SELECT count(*) FROM live_snapshots").fetchone()[0],
            "failed_live_polls": self.db.execute(
                "SELECT count(*) FROM live_runs WHERE state='failed'"
            ).fetchone()[0],
            "latest_successful_poll": self.db.execute(
                "SELECT max(fetched_at) FROM live_runs WHERE state='complete'"
            ).fetchone()[0],
        }

    def record_feed(
        self,
        source: str,
        document: dict[str, Any] | None,
        demo: bool = False,
        error: str | None = None,
        stations: set[str] | None = None,
    ) -> None:
        import uuid

        from app.modules.ingestion.live import status_records, timestamp

        fetched = datetime.now(UTC).isoformat()
        run = str(uuid.uuid4())
        rows, rejected = status_records(document) if document else ([], 0)
        updated = timestamp(document["last_updated"]).isoformat() if document else None
        self.db.execute(
            "INSERT INTO live_runs VALUES(?,?,?,?,?,?,?,?)",
            (
                run,
                source,
                fetched,
                updated,
                "failed" if error else "complete",
                demo,
                rejected,
                error,
            ),
        )
        for row in rows:
            if stations is not None and row["external_id"] not in stations:
                continue
            body = {
                **row,
                "reported_at": row["reported_at"].isoformat(),
                "feed_updated_at": updated,
                "fetched_at": fetched,
                "is_demo": demo,
            }
            self.db.execute(
                "INSERT OR IGNORE INTO live_snapshots VALUES(?,?,?,?,?)",
                (row["external_id"], body["reported_at"], demo, run, json.dumps(body)),
            )
        self.db.commit()

    def features(self, start: str, end: str, stations: set[str]) -> Iterator[dict[str, Any]]:
        """Dense point-in-time demand with nulls for unverified source coverage."""
        lower, upper = local_time(start), local_time(end)
        if lower >= upper:
            raise ValueError("Invalid feature range")
        self.db.create_function("bucket", 1, bucket, deterministic=True)
        runs = [dict(r) for r in self.db.execute("SELECT * FROM runs WHERE state='complete'")]
        for station in sorted(stations):
            intervals = []
            for run in runs:
                scope = json.loads(run["scope"])
                if station not in scope["stations"]:
                    continue
                # Only known official monthly NYC archives establish real date coverage.
                match = re.search(r"/(\d{6})-citibike-tripdata\.zip$", run["source"])
                begin, finish = local_time(scope["start"]), local_time(scope["end"])
                if match:
                    month = datetime.strptime(match[1], "%Y%m").replace(tzinfo=NY)
                    next_month = month.replace(
                        year=month.year + (month.month == 12), month=month.month % 12 + 1
                    )
                    begin, finish = (
                        max(begin, month.astimezone(UTC)),
                        min(finish, next_month.astimezone(UTC)),
                    )
                elif not run["demo"]:
                    continue
                intervals.append((begin, finish, bool(run["demo"])))
            merged: list[tuple[datetime, datetime, bool]] = []
            for begin, finish, demo in sorted(intervals):
                if merged and begin <= merged[-1][1] and demo == merged[-1][2]:
                    merged[-1] = (merged[-1][0], max(finish, merged[-1][1]), demo)
                else:
                    merged.append((begin, finish, demo))
            intervals = merged
            cursor = self.db.execute(
                """SELECT bucket(at) bucket_start,
              sum(d) departures, sum(a) arrivals FROM (
              SELECT started_at at, 1 d, 0 a FROM trips
                WHERE start_station=? AND started_at>=? AND started_at<?
              UNION ALL SELECT ended_at, 0, 1 FROM trips
                WHERE end_station=? AND ended_at>=? AND ended_at<?)
              GROUP BY bucket(at) ORDER BY bucket_start""",
                (
                    station,
                    lower.isoformat(),
                    upper.isoformat(),
                    station,
                    lower.isoformat(),
                    upper.isoformat(),
                ),
            )
            observation = cursor.fetchone()
            at = lower
            while at < upper:
                if observation and observation["bucket_start"] < at.isoformat():
                    observation = cursor.fetchone()
                    continue
                present = observation is not None and observation["bucket_start"] == at.isoformat()
                covered = any(b <= at and at + timedelta(minutes=15) <= e for b, e, _ in intervals)
                # Adjacent-month carry-in rides are not inferred; maximum accepted duration is 24h.
                arrivals_covered = any(
                    b + timedelta(days=1) <= at and at + timedelta(minutes=15) <= e
                    for b, e, _ in intervals
                )
                yield {
                    "station_id": station,
                    "bucket_start": at.isoformat(),
                    "departures": observation["departures"]
                    if present and (covered or observation["departures"] > 0)
                    else (0 if covered else None),
                    "arrivals": observation["arrivals"]
                    if present and (arrivals_covered or observation["arrivals"] > 0)
                    else (0 if arrivals_covered else None),
                    "departure_coverage": covered,
                    "arrival_coverage": arrivals_covered,
                    "is_demo": any(d for b, e, d in intervals if b <= at < e),
                }
                if present:
                    observation = cursor.fetchone()
                at += timedelta(minutes=15)

    def demand(self, run_id: str) -> Iterator[dict[str, Any]]:
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise ValueError("Unknown run")
        scope = json.loads(run["scope"])
        self.db.create_function("bucket", 1, bucket, deterministic=True)
        self.db.execute("PRAGMA temp_store=FILE")
        cursor = self.db.execute(
            """SELECT station, bucket(at) bucket_start,
          sum(departures) departures, sum(arrivals) arrivals FROM (
          SELECT start_station station, started_at at, 1 departures, 0 arrivals
          FROM trips JOIN provenance USING(ride_id) WHERE run_id=?
          UNION ALL SELECT end_station, ended_at, 0, 1
          FROM trips JOIN provenance USING(ride_id) WHERE run_id=?)
          WHERE station IN (SELECT value FROM json_each(?))
          GROUP BY station, bucket(at) ORDER BY station, bucket_start""",
            (run_id, run_id, json.dumps(scope["stations"])),
        )
        for row in cursor:
            yield {
                "station_id": row["station"],
                "bucket_start": row["bucket_start"],
                "departures": row["departures"],
                "arrivals": row["arrivals"],
                "run_id": run_id,
                "complete": run["state"] == "complete",
                "is_demo": bool(run["demo"]),
            }
