from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.modules.ingestion.history import Stage, local_time, normalize
from app.modules.ingestion.live import stale, status_records, walking_distance
from app.modules.ingestion.sources import SourceClient

FIXTURES = Path(__file__).parents[1] / "app" / "modules" / "ingestion" / "fixtures"
UTC = timezone.utc  # noqa: UP017 -- local Python 3.10 verification


def archive(tmp_path: Path) -> Path:
    path = tmp_path / "demo.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.write(FIXTURES / "trips.csv", "part-1.csv")
        zipped.write(FIXTURES / "trips.csv", "part-2.csv")
    return path


def test_resume_duplicates_bad_records_and_aggregate(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.sqlite3")
    path = archive(tmp_path)
    args = (path, "2025-01-01", "2025-01-02", {"DEMO-A", "DEMO-B"})
    run = stage.ingest(*args, chunk_size=2, max_rows=2, demo=True)
    assert stage.report()["runs"][0]["state"] == "running"
    assert all(not row["complete"] for row in stage.demand(run))
    stage.db.close()
    stage = Stage(tmp_path / "stage.sqlite3")
    assert stage.ingest(*args, chunk_size=2, demo=True) == run
    assert stage.report()["unique_trips"] == 2
    report = stage.report()["runs"][0]
    assert report["rows_seen"] == 10
    assert report["invalid"] == 4
    assert report["state"] == "complete"
    demand = list(stage.demand(run))
    assert sum(d["departures"] for d in demand) == 2
    assert sum(d["arrivals"] for d in demand) == 2
    assert all(d["is_demo"] for d in demand)
    stage.ingest(*args, demo=True)
    assert stage.report()["unique_trips"] == 2


@pytest.mark.parametrize("value", ["2025-03-09 02:30:00", "2025-11-02 01:30:00"])
def test_dst_unresolvable_times_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        local_time(value)


def test_timezone_aliases_and_stale_inventory() -> None:
    assert local_time("2025-01-01 08:00:00").hour == 13
    assert local_time("2025-07-01 08:00:00").hour == 12
    assert local_time("2025-11-02T01:30:00-04:00").hour == 5
    rows, rejected = status_records(json.loads((FIXTURES / "status.json").read_text()))
    assert rejected == 0 and rows[1]["bikes"] == 0
    now = datetime.now(UTC)
    assert stale(rows[0]["reported_at"], rows[0]["feed_updated_at"], now, now)
    assert not stale(now, now, now, now)
    assert stale(now, now, now, now + timedelta(seconds=301))
    row = dict(
        zip(
            [
                "ride_id",
                "started_at",
                "ended_at",
                "start_station_id",
                "end_station_id",
                "start_lat",
                "start_lng",
                "end_lat",
                "end_lng",
            ],
            [
                "DEMO",
                "2025-01-01 08:00:00",
                "2025-01-01 08:15:00",
                "old",
                "DEMO-B",
                "40",
                "-73",
                "40",
                "-73",
            ],
            strict=True,
        )
    )
    assert normalize(row, {"old": "DEMO-A"})["start_station"] == "DEMO-A"


def test_geography() -> None:
    assert walking_distance(40.7, -74, 40.7, -74) == 0
    assert 140 < walking_distance(40.7, -74, 40.701, -74) < 150


def test_discovery_and_network_failure() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "en": {
                        "feeds": [
                            {"name": "station_information", "url": "https://example.org/info"},
                            {"name": "station_status", "url": "https://example.org/status"},
                        ]
                    }
                }
            },
        )

    source = SourceClient(httpx.Client(transport=httpx.MockTransport(respond)))
    assert source.feeds()["station_status"].endswith("/status")
    source = SourceClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "300"})
            )
        )
    )
    with pytest.raises(RuntimeError, match="rate limited"):
        source.feeds()


def test_rate_limit_without_retry_header_is_respectful(monkeypatch: pytest.MonkeyPatch) -> None:
    delays = []
    monkeypatch.setattr("app.modules.ingestion.sources.time.sleep", delays.append)
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200)

    source = SourceClient(httpx.Client(transport=httpx.MockTransport(respond)))
    assert source.get("https://example.org/status").status_code == 200
    assert delays == [60]


def test_schema_rejection(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr("bad.csv", "ride_id,unexpected\na,b\n")
    with pytest.raises(ValueError, match="schema"):
        Stage(tmp_path / "stage.db").ingest(path, "2025-01-01", "2025-02-01", {"DEMO-A"})


def test_truncated_csv_records_are_excluded(tmp_path: Path) -> None:
    path = tmp_path / "truncated.zip"
    content = (FIXTURES / "trips.csv").read_text()
    content += "DEMO-TRUNCATED,2025-01-01 08:00:00,2025-01-01 08:20:00\n"
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr("trips.csv", content)
    stage = Stage(tmp_path / "stage.db")
    stage.ingest(path, "2025-01-01", "2025-01-02", {"DEMO-A"}, demo=True)
    report = stage.report()["runs"][0]
    assert report["state"] == "complete" and report["invalid"] == 3
    assert json.loads(report["errors"])["missing_station_id"] == 2


def test_entirely_invalid_archive_cannot_establish_zero_coverage(tmp_path: Path) -> None:
    path = tmp_path / "invalid.zip"
    lines = (FIXTURES / "trips.csv").read_text().splitlines()
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr("trips.csv", lines[0] + "\n" + lines[4] + "\n")
    stage = Stage(tmp_path / "stage.db")
    with pytest.raises(ValueError, match="no valid source observations"):
        stage.ingest(path, "2025-01-01", "2025-01-02", {"DEMO-A"}, demo=True)
    assert stage.report()["runs"][0]["state"] == "rejected"
    assert all(
        r["departures"] is None for r in stage.features("2025-01-01", "2025-01-02", {"DEMO-A"})
    )


def test_conflicting_trip_is_excluded_without_false_provenance(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.db")
    stage.ingest(archive(tmp_path), "2025-01-01", "2025-01-02", {"DEMO-A"}, demo=True)
    path = tmp_path / "conflict.zip"
    content = (FIXTURES / "trips.csv").read_text().replace("08:01:00", "08:02:00")
    with zipfile.ZipFile(path, "w") as zipped:
        zipped.writestr("conflicting.csv", content)
    run = stage.ingest(path, "2025-01-01", "2025-01-02", {"DEMO-A"}, demo=True)
    report = next(r for r in stage.report()["runs"] if r["id"] == run)
    assert json.loads(report["errors"])["conflicting_duplicate"] == 2
    assert (
        stage.db.execute("SELECT count(*) FROM provenance WHERE run_id=?", (run,)).fetchone()[0]
        == 1
    )
    assert stage.report()["unique_trips"] == 2


def test_missing_coverage_is_not_zero(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.db")
    stage.ingest(archive(tmp_path), "2025-01-01", "2025-01-03", {"DEMO-A"}, demo=True)
    rows = list(stage.features("2025-01-01", "2025-01-04", {"DEMO-A"}))
    assert rows[0]["departures"] == 0 and rows[0]["departure_coverage"]
    assert rows[0]["arrivals"] is None and not rows[0]["arrival_coverage"]
    assert rows[-1]["departures"] is None and not rows[-1]["departure_coverage"]
    departure_only = next(r for r in rows if r["departures"] == 2)
    assert departure_only["arrivals"] is None


def test_dst_daily_bucket_counts_and_partial_observations(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.db")
    assert len(list(stage.features("2025-03-09", "2025-03-10", {"A"}))) == 92
    assert len(list(stage.features("2025-11-02", "2025-11-03", {"A"}))) == 100
    stage.ingest(archive(tmp_path), "2025-01-01", "2025-01-02", {"DEMO-A"}, max_rows=1)
    row = next(
        r for r in stage.features("2025-01-01", "2025-01-02", {"DEMO-A"}) if r["departures"] == 1
    )
    assert not row["departure_coverage"] and row["arrivals"] is None


def test_adjacent_archives_cover_arrival_carry_in(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.db")
    path = archive(tmp_path)
    stage.ingest(path, "2025-01-01", "2025-01-02", {"DEMO-A"}, demo=True)
    stage.ingest(path, "2025-01-02", "2025-01-03", {"DEMO-A"}, demo=True)
    row = next(stage.features("2025-01-02", "2025-01-03", {"DEMO-A"}))
    assert row["arrival_coverage"] and row["arrivals"] == 0


def test_v3_inventory_and_invalid_records() -> None:
    document = {
        "version": "3.0",
        "last_updated": "2025-01-01T13:00:00Z",
        "ttl": 60,
        "data": {
            "stations": [
                {
                    "station_id": "A",
                    "last_reported": "2025-01-01T13:00:00Z",
                    "num_vehicles_available": 2,
                    "num_docks_available": 3,
                    "is_installed": True,
                    "is_renting": True,
                    "is_returning": False,
                },
                {
                    "station_id": "B",
                    "last_reported": "2025-01-01T13:00:00Z",
                    "num_vehicles_available": -1,
                    "num_docks_available": 3,
                },
            ]
        },
    }
    rows, rejected = status_records(document)
    assert rejected == 1 and len(rows) == 1
    assert rows[0]["bikes"] == 2 and not rows[0]["is_returning"]


def test_download_ranges_are_joined(tmp_path: Path) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.headers["Range"])
        if len(requests) == 1:
            return httpx.Response(
                206, content=b"abc", headers={"ETag": '"etag"', "Content-Range": "bytes 0-2/6"}
            )
        assert request.headers["If-Range"] == '"etag"'
        return httpx.Response(
            206, content=b"def", headers={"ETag": '"etag"', "Content-Range": "bytes 3-5/6"}
        )

    source = SourceClient(httpx.Client(transport=httpx.MockTransport(respond)))
    path = source.download("fixture.zip", tmp_path, max_bytes=100)
    assert path.read_bytes() == b"abcdef" and len(requests) == 2


def test_offline_snapshot_consistency_and_failure_audit(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.db")
    document = json.loads((FIXTURES / "status.json").read_text())
    stage.record_feed("synthetic", document, True)
    stage.record_feed("synthetic", document, True)
    stage.record_feed("synthetic", None, True, "network unavailable")
    assert stage.report()["live_snapshots"] == 2
    assert (
        stage.db.execute("SELECT count(*) FROM live_runs WHERE state='failed'").fetchone()[0] == 1
    )


def test_live_history_retains_only_selected_stations(tmp_path: Path) -> None:
    stage = Stage(tmp_path / "stage.db")
    stage.record_feed(
        "synthetic", json.loads((FIXTURES / "status.json").read_text()), True, stations={"DEMO-A"}
    )
    assert stage.report()["live_snapshots"] == 1
    assert stage.db.execute("SELECT station_id FROM live_snapshots").fetchone()[0] == "DEMO-A"
