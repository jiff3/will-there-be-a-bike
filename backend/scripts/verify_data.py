"""Independently reconcile retained ride endpoints and demand exports (offline)."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    db = sqlite3.connect(args.directory / "stage.sqlite3")
    db.row_factory = sqlite3.Row
    result = []
    for run in db.execute("SELECT * FROM runs"):
        scope = json.loads(run["scope"])
        expected = db.execute(
            """SELECT
          sum(start_station IN (SELECT value FROM json_each(?))) departures,
          sum(end_station IN (SELECT value FROM json_each(?))) arrivals
          FROM trips JOIN provenance USING(ride_id) WHERE run_id=?""",
            (json.dumps(scope["stations"]), json.dumps(scope["stations"]), run["id"]),
        ).fetchone()
        path = args.directory / f"demand-{run['id'][:12]}.jsonl"
        # Independent SQL bucketing (not ingestion's Python UDF), compared row by row.
        expected_bins = db.execute(
            """SELECT station,
          strftime('%Y-%m-%dT%H:', at) ||
            printf('%02d', CAST(strftime('%M', at) AS INTEGER)/15*15) || ':00+00:00' bucket,
          sum(d) departures, sum(a) arrivals FROM (
            SELECT start_station station, started_at at, 1 d, 0 a
            FROM trips JOIN provenance USING(ride_id) WHERE run_id=?
            UNION ALL SELECT end_station, ended_at, 0, 1
            FROM trips JOIN provenance USING(ride_id) WHERE run_id=?)
          WHERE station IN (SELECT value FROM json_each(?))
          GROUP BY station, bucket ORDER BY station, bucket""",
            (run["id"], run["id"], json.dumps(scope["stations"])),
        )
        counts = {"departures": 0, "arrivals": 0}
        bins = 0
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                assert row["run_id"] == run["id"]
                assert row["bucket_start"].endswith("+00:00")
                assert int(row["bucket_start"][14:16]) % 15 == 0
                assert row["complete"] == (run["state"] == "complete")
                expected_bin = expected_bins.fetchone()
                assert expected_bin is not None
                assert tuple(expected_bin) == (
                    row["station_id"],
                    row["bucket_start"],
                    row["departures"],
                    row["arrivals"],
                )
                for field in counts:
                    counts[field] += row[field]
                bins += 1
        assert expected_bins.fetchone() is None
        assert all(counts[field] == expected[field] for field in counts)
        assert sum(json.loads(run["errors"]).values()) == run["invalid"]
        assert (
            db.execute(
                "SELECT sum(row_number) FROM checkpoints WHERE run_id=?", (run["id"],)
            ).fetchone()[0]
            == run["rows_seen"]
        )
        result.append(
            {
                "source": run["source"],
                "sha256": run["checksum"],
                "state": run["state"],
                "rows_scanned": run["rows_seen"],
                "excluded": run["invalid"],
                "exclusions": json.loads(run["errors"]),
                "demand_bins": bins,
                **counts,
            }
        )
    print(
        json.dumps(
            {
                "reconciliation": result,
                "unique_trips": db.execute("SELECT count(*) FROM trips").fetchone()[0],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
