"""Run with python -m app.modules.ingestion.cli; network only for explicit commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
import zipfile
from collections.abc import Coroutine
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypeVar

from app.core.config import get_settings
from app.modules.ingestion.history import Stage
from app.modules.ingestion.live import status_records, walking_distance
from app.modules.ingestion.sources import SourceClient

T = TypeVar("T")


def database_task(coroutine: Coroutine[Any, Any, T]) -> T:  # noqa: UP047 -- Python 3.10 smoke runner
    async def run() -> T:
        from app.persistence.database import engine

        try:
            return await coroutine
        finally:
            await engine.dispose()

    return asyncio.run(run())


def months(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first >= last:
        raise ValueError("End must follow start (end is exclusive)")
    current = first.replace(day=1)
    result = []
    while current < last:
        result.append(current.strftime("%Y-%m"))
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Citi Bike data infrastructure")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    staged = commands.add_parser(
        "publish-staged", help="Publish completed disk checkpoints without redownloading archives"
    )
    staged.add_argument("--run-id", help="Single complete staged run; default all completed runs")
    stations = commands.add_parser("stations", help="Discover/save catalog, optionally publish")
    stations.add_argument("--limit", type=int, default=100)
    stations.add_argument("--lat", type=float, default=40.748)
    stations.add_argument("--lon", type=float, default=-73.985)
    stations.add_argument("--publish", action="store_true")
    stations.add_argument("--discovery", default=get_settings().gbfs_discovery_url)
    backfill = commands.add_parser(
        "backfill", help="Download monthly archives, stage and aggregate"
    )
    backfill.add_argument("--start", default="2025-01-01")
    backfill.add_argument("--end", default="2025-03-01")
    backfill.add_argument("--stations", type=Path, help="Catalog JSON or JSON list of station IDs")
    backfill.add_argument(
        "--aliases", type=Path, help="Reviewed old-ID to canonical-ID JSON mapping"
    )
    backfill.add_argument("--archive", type=Path, help="Offline/local ZIP instead of download")
    backfill.add_argument("--chunk-size", type=int, default=5000)
    backfill.add_argument("--max-rows", type=int, help="Smoke only: leaves run incomplete")
    backfill.add_argument("--keep-raw", action="store_true")
    backfill.add_argument("--publish", action="store_true")
    backfill.add_argument("--demo", action="store_true")
    live = commands.add_parser("collect", help="Collect once, or poll with --watch")
    live.add_argument("--discovery", default=get_settings().gbfs_discovery_url)
    live.add_argument("--fixture", type=Path)
    live.add_argument("--publish", action="store_true")
    live.add_argument("--watch", action="store_true")
    live.add_argument("--interval", type=int, default=get_settings().gbfs_poll_seconds)
    validation = commands.add_parser(
        "validate", help="Report per-run completeness, exclusions and counts"
    )
    validation.add_argument("--start", help="Check requested feature coverage from this local date")
    validation.add_argument("--end", help="Exclusive local date for coverage validation")
    aggregate = commands.add_parser(
        "aggregate", help="Export dense 15-minute features with coverage masks"
    )
    aggregate.add_argument("--start", default="2025-01-01")
    aggregate.add_argument("--end", default="2025-03-01")
    demo_parser = commands.add_parser(
        "seed-demo", help="Seed explicitly synthetic offline trips/inventory"
    )
    demo_parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    stage = Stage(args.data_dir / "stage.sqlite3")
    source = SourceClient()
    if args.command == "publish-staged":
        from app.modules.ingestion.persistence import publish, update_stations

        catalog = json.loads((args.data_dir / "stations.json").read_text())
        runs = [row for row in stage.report()["runs"] if row["state"] == "complete"]
        if args.run_id:
            runs = [row for row in runs if row["id"] == args.run_id]
        if not runs:
            raise ValueError("No matching completed staged run")

        async def publish_completed() -> None:
            demo_values = {bool(row["demo"]) for row in runs}
            if len(demo_values) != 1:
                raise ValueError("Publish real and demo catalogs separately")
            await update_stations(catalog, demo_values.pop())
            for row in runs:
                await publish(stage, row["id"])
                print(json.dumps({"published_run": row["id"], "state": row["state"]}), flush=True)

        database_task(publish_completed())
    elif args.command == "seed-demo":
        fixtures = Path(__file__).parent / "fixtures"
        demo_archive = args.data_dir / "synthetic-demo.zip"
        with zipfile.ZipFile(demo_archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            info = zipfile.ZipInfo("synthetic-trips.csv", date_time=(2025, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, (fixtures / "trips.csv").read_bytes())
        catalog = json.loads((fixtures / "stations.json").read_text())
        run = stage.ingest(
            demo_archive, "2025-01-01", "2025-01-02", {"DEMO-A", "DEMO-B"}, demo=True
        )
        shutil.copyfile(fixtures / "stations.json", args.data_dir / "stations.json")
        shutil.copyfile(fixtures / "status.json", args.data_dir / "latest-status.json")
        stage.record_feed(
            "synthetic-demo", json.loads((fixtures / "status.json").read_text()), True
        )
        if args.publish:
            from app.modules.ingestion.persistence import collect, publish, update_stations

            async def seed() -> None:
                await update_stations(catalog, True)
                await collect(
                    json.loads((fixtures / "status.json").read_text()), "synthetic-demo", True
                )
                await publish(stage, run)

            database_task(seed())
        print(json.dumps({"is_demo": True, **stage.report(), "demand": list(stage.demand(run))}))
    elif args.command == "aggregate":
        catalog = json.loads((args.data_dir / "stations.json").read_text())
        feature_stations = {str(s["station_id"]) for s in catalog["data"]["stations"]}
        feature_rows = 0
        with (args.data_dir / "features.jsonl").open("w", encoding="utf-8") as output:
            for row in stage.features(args.start, args.end, feature_stations):
                output.write(json.dumps(row) + "\n")
                feature_rows += 1
        print(
            json.dumps(
                {"feature_rows": feature_rows, "path": str(args.data_dir / "features.jsonl")}
            )
        )
    elif args.command == "validate":
        report = stage.report()
        if bool(args.start) != bool(args.end):
            parser.error("Coverage validation requires both --start and --end")
        missing = 0
        missing_arrivals = 0
        if args.start:
            catalog = json.loads((args.data_dir / "stations.json").read_text())
            for feature in stage.features(
                args.start, args.end, {str(s["station_id"]) for s in catalog["data"]["stations"]}
            ):
                if not feature["departure_coverage"]:
                    missing += 1
                if not feature["arrival_coverage"]:
                    missing_arrivals += 1
        report["uncovered_departure_bins"] = missing if args.start else None
        report["uncovered_arrival_bins"] = missing_arrivals if args.start else None
        print(json.dumps(report, indent=2))
        if missing or not report["runs"] or any(r["state"] != "complete" for r in report["runs"]):
            raise SystemExit(2)
    elif args.command == "stations":
        if not 1 <= args.limit <= 5000:
            parser.error("Station limit must be 1..5000")
        feeds = source.feeds(args.discovery)
        document = source.get(feeds["station_information"]).json()
        selected = sorted(
            document["data"]["stations"],
            key=lambda s: walking_distance(args.lat, args.lon, float(s["lat"]), float(s["lon"])),
        )[: args.limit]
        document["data"]["stations"] = selected
        (args.data_dir / "stations.json").write_text(json.dumps(document), encoding="utf-8")
        print(
            json.dumps(
                {
                    "source": feeds["station_information"],
                    "stations": len(selected),
                    "version": document.get("version"),
                }
            )
        )
        if args.publish:
            from app.modules.ingestion.persistence import update_stations

            print(database_task(update_stations(document)))
    elif args.command == "backfill":
        catalog = json.loads(
            (args.stations or args.data_dir / "stations.json").read_text(encoding="utf-8")
        )
        aliases: dict[str, str] = {}
        if isinstance(catalog, list):
            selected_ids = set(map(str, catalog))
        else:
            selected_ids = set()
            for station in catalog["data"]["stations"]:
                canonical = str(station["station_id"])
                selected_ids.add(canonical)
                alias = str(station.get("short_name") or canonical)
                if alias in aliases and aliases[alias] != canonical:
                    raise ValueError(f"Ambiguous catalog station alias: {alias}")
                aliases[alias] = canonical
        if args.aliases:
            aliases.update(json.loads(args.aliases.read_text(encoding="utf-8")))
        if any(
            not isinstance(k, str) or not isinstance(v, str) or not k or not v
            for k, v in aliases.items()
        ):
            raise ValueError("Alias mappings must contain nonempty string IDs")
        if not 1 <= len(selected_ids) <= 5000 or args.chunk_size < 1:
            raise ValueError("Station scope must be 1..5000 and chunk size must be positive")
        for month in months(args.start, args.end):
            archives = [(str(args.archive), 0)] if args.archive else source.archives(month)
            for key, size in archives:
                if size > 2_000_000_000:
                    raise ValueError("Source archive exceeds 2 GB budget; use a smaller range")
                archive = args.archive or source.download(key, args.data_dir / "raw")
                if shutil.disk_usage(args.data_dir).free < 3_000_000_000:
                    raise ValueError("Less than 3 GB free: refusing to expand staging data")
                run = stage.ingest(
                    archive,
                    args.start,
                    args.end,
                    selected_ids,
                    aliases,
                    args.chunk_size,
                    args.demo,
                    args.max_rows,
                    key if args.archive else f"https://s3.amazonaws.com/tripdata/{key}",
                )
                run_report = dict(
                    stage.db.execute("SELECT * FROM runs WHERE id=?", (run,)).fetchone()
                )
                print(
                    json.dumps(
                        {
                            "phase": "staged",
                            "run_id": run,
                            "source": run_report["source"],
                            "state": run_report["state"],
                            "rows_seen": run_report["rows_seen"],
                            "invalid": run_report["invalid"],
                            "errors": json.loads(run_report["errors"]),
                            "is_demo": bool(run_report["demo"]),
                        }
                    ),
                    flush=True,
                )
                with (args.data_dir / f"demand-{run[:12]}.jsonl").open(
                    "w", encoding="utf-8"
                ) as output:
                    for observation in stage.demand(run):
                        output.write(json.dumps(observation) + "\n")
                if args.publish:
                    from app.modules.ingestion.persistence import publish

                    database_task(publish(stage, run))
                state = stage.db.execute("SELECT state FROM runs WHERE id=?", (run,)).fetchone()[0]
                if not args.keep_raw and not args.archive and state == "complete":
                    archive.unlink()  # Checksum, trips, provenance and aggregate retained in stage.
            if args.archive:
                break
    elif args.command == "collect":
        catalog_path = args.data_dir / "stations.json"
        if not catalog_path.exists() and not args.fixture:
            parser.error("Run stations first to configure the retained live station scope")
        live_scope = (
            {str(s["station_id"]) for s in json.loads(catalog_path.read_text())["data"]["stations"]}
            if catalog_path.exists()
            else None
        )
        while True:
            try:
                if args.fixture:
                    document = json.loads(args.fixture.read_text(encoding="utf-8"))
                    url = str(args.fixture)
                else:
                    feeds = source.feeds(args.discovery)
                    url = feeds["station_status"]
                    document = source.get(url).json()
                rows, rejected = status_records(document)
                stage.record_feed(url, document, bool(args.fixture), stations=live_scope)
                retained = sum(
                    live_scope is None or row["external_id"] in live_scope for row in rows
                )
                report = {
                    "source": url,
                    "version": document.get("version"),
                    "stations": len(rows),
                    "retained_stations": retained,
                    "missing_selected_stations": len(live_scope) - retained if live_scope else 0,
                    "rejected": rejected,
                    "is_demo": bool(args.fixture),
                }
                if args.publish:
                    from app.modules.ingestion.persistence import collect

                    report.update(database_task(collect(document, url, bool(args.fixture))))
                # Persist payload even offline; filenames do not grow without bound by default.
                (args.data_dir / "latest-status.json").write_text(
                    json.dumps(document), encoding="utf-8"
                )
                print(json.dumps(report), flush=True)
                interval = max(30, args.interval, int(document["ttl"]))
            except Exception as error:
                stage.record_feed(args.discovery, None, bool(args.fixture), str(error))
                if not args.watch:
                    raise
                print(json.dumps({"error": str(error), "state": "unavailable"}), flush=True)
                interval = max(120, args.interval, getattr(error, "retry_after", 0))
            if not args.watch:
                break
            time.sleep(interval)
    stage.db.close()
    source.client.close()


if __name__ == "__main__":
    main()
