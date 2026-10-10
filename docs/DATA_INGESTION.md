# Citi Bike data operations

## Verified sources

Checked against the operator's [system data page](https://citibikenyc.com/system-data) on October 9, 2026 (Pacific time):

- Historical downloads: [official tripdata bucket](https://s3.amazonaws.com/tripdata/index.html). The importer enumerates S3 ListObjectsV2 keys with a month prefix, including continuation tokens; it does not guess filenames. The January 2025 NYC archive is `202501-citibike-tripdata.zip`, 414,213,312 compressed bytes.
- Discovery: [operator-linked GBFS 2.3](https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json). The observed discovery points to `https://gbfs.lyft.com/gbfs/2.3/bkn/en/station_information.json` and `https://gbfs.lyft.com/gbfs/2.3/bkn/en/station_status.json`. These child URLs are discovered on each collection, not hardcoded.
- [GBFS 2.3 field specification](https://github.com/MobilityData/gbfs/blob/v2.3/gbfs.md) and [current 3.0 specification](https://gbfs.org/documentation/reference/). The parser supports numeric timestamps and `num_bikes_available` in the deployed feed, plus RFC3339 timestamps and `num_vehicles_available` in v3 envelopes. Inventory excludes disabled vehicles/docks as defined by GBFS. Renting/returning/installed flags are returned separately.
- Data is subject to the operator's [data use policy](https://citibikenyc.com/data-sharing-policy); the repository's MIT license covers code, not downloaded operator data.

## Start a manageable real dataset

Run from the repository root after `Copy-Item .env.example .env` and `docker compose up --build`. Commands below use the running API container, which has a persistent `/app/data` mount. Training never runs in this container.

```powershell
docker compose exec backend python -m app.modules.ingestion.cli stations --limit 100 --publish
docker compose exec backend python -m app.modules.ingestion.cli backfill --start 2025-01-01 --end 2025-03-01 --publish
docker compose exec backend python -m app.modules.ingestion.cli aggregate --start 2025-01-01 --end 2025-03-01
docker compose exec backend python -m app.modules.ingestion.cli validate --start 2025-01-01 --end 2025-03-01
docker compose exec backend python -m app.modules.ingestion.cli collect --publish
docker compose exec backend python -m app.modules.ingestion.cli collect --publish --watch --interval 60
```

`stations` selects the nearest 100 stations to Midtown using a great-circle approximation; change `--lat`, `--lon`, or `--limit`. `backfill` accepts a saved catalog or a JSON array of station IDs with `--stations`; reuse this exact selection for reproducibility. The default backfill covers two months, January and February 2025, with an exclusive end date. Only trips touching the selected stations are retained. All CSV members are scanned, without extracting them to disk. Modern fields and legacy `starttime`/`stoptime` exports are validated explicitly.

For native development, use Python 3.12, `pip install -e "backend[dev]"`, then run the same module commands from `backend/` with `--data-dir ../data/real`. PostgreSQL publication is optional: staging, aggregation, validation, station discovery and live parsing also run without a database. `--publish` requires the migration and a reachable `DATABASE_URL`.

## Resume and resources

Downloads use bounded 32 MiB HTTP ranges and ETag/If-Range validators. Unsupported ranges restart safely. A completed download is atomically renamed; ZIP CRCs are verified while CSV members are consumed. Four consecutive transport failures stop the download. Long Retry-After responses stop rather than immediately retry. Re-run the same command to resume.

The staging database uses SQLite solely as a disk-backed import workspace. PostgreSQL/PostGIS remains the application's database. A run identity hashes archive contents, date range, selected station IDs, aliases, and demo flag. Every 5,000 source rows, normalized trips, provenance and CSV-member row checkpoints commit together. Resuming seeks by replaying the compressed member to its checkpoint (bounded RAM, some extra CPU). Ride IDs deduplicate across members, archives and repeats; conflicting content is excluded with a quality counter. Legacy ride identity uses a full-row SHA-256 fingerprint. A bounded 256 MiB database cache reduces index reads on Windows disks while leaving ample memory for serving.

The staging WAL checkpoints at approximately 40 MiB instead of SQLite's default 4 MiB, reducing random disk writes while retaining full commit durability. `--chunk-size 20000` commits source-row checkpoints less frequently on slow Windows disks; rows are still streamed one at a time, not buffered into a 20,000-row Python list. Expect more replay after interruption with larger chunks. No synchronous/durability setting is weakened. See [SQLite WAL behavior](https://sqlite.org/wal.html).

Raw downloads are deleted after successful complete ingestion (and publication if requested), unless `--keep-raw` is used. Interrupted downloads/checkpoints remain recoverable. A 2 GB compressed-file ceiling, disk headroom checks, 256 MiB SQLite cache and disk-based aggregation keep default processing small. Expand ranges deliberately and monitor staging/PostgreSQL disk. The application scheduler now performs bounded hourly snapshot retention (30 days by default); standalone collectors still require that scheduler or explicit operator retention. Retention is not backup. SQL indexes serve station/time and PostGIS radius queries; partitioning is deferred until warranted. Run one backfill per staging directory; SQLite serializes writers and checkpoints allow interrupted work to continue.

## Time, identities, and coverage

Historical timestamps without offsets are interpreted in `America/New_York`; stored times and bucket boundaries are UTC. Spring-forward nonexistent times and fall-back ambiguous times are quarantined. Offset-bearing records remain unambiguous, including either fall-back occurrence. Fifteen-minute UTC bins give 92 or 100 bins per local DST-transition day rather than assuming 96.

Station IDs remain strings, including decimal IDs. GBFS `short_name` maps trip station identifiers to GBFS canonical IDs. Reviewed identifier changes can be supplied with `--aliases path/to/old-to-canonical.json`; names alone never merge stations. Missing IDs/coordinates and invalid durations are excluded and counted, so historical closures, renamed IDs and missing data cannot become invented inventory. Historical IDs without a catalog match may be retained with an explicit station subset; the raw ID remains a usable demand key, without an invented location.

Sparse `demand-*.jsonl` files record observed valid departures/arrivals with run provenance. `features.jsonl` provides a dense station/15-minute grid and separate departure/arrival coverage flags. Null denotes unverified coverage; zero denotes no valid published trips in a covered interval. Completed known official monthly archives establish departure coverage, while custom/partial sources do not. Adjacent covered archives merge into continuous coverage; arrival coverage conservatively excludes the first 24 hours of each continuous range to account for carry-in trips (accepted rides are at most 24 hours). Positive observed counts remain visible even where coverage is partial, but an uncovered zero is null. Run completion means the archive was read, not that every source record was valid or that a station was operational. Exclusion counters must accompany model evaluation.

Trip demand is observed rides only. It does not reveal bike or dock stockouts, abandoned attempts, rebalancing or historic inventory. Only measured GBFS snapshots supply inventory. No historical inventory is reconstructed from trip totals.

Run-scoped demand preserves lineage but must not be summed across overlapping runs. The dense feature exporter deduplicates ride IDs globally instead. Current station metadata is a selection/catalog source, not an as-of historical catalog; historical station operating states are unknown. Inventory forecasting needs future measured snapshots as labels, not retrospectively invented 2025 inventory. Coverage validation reports both directional gaps; expected arrival carry-in gaps do not fail otherwise complete departure coverage.

## Live collection and spatial API

`collect` discovers feeds and records publisher update, station report and consumer fetch timestamps. Polling respects the maximum of feed TTL, configured interval and a 30-second floor. Rate limits and temporary network errors receive bounded retry/backoff; watch mode logs failures and backs off to at least 120 seconds. PostgreSQL advisory locks prevent concurrent publication; `(station, reported_at, is_demo)` prevents repeated measurements from duplicating snapshots.

Live staging retains only IDs in the saved `stations.json` catalog (100 by default). Latest payload overwrites rather than appends. Run `stations` before a real collector; otherwise it refuses an unbounded city-wide history. Collection reports coverage/missing stations; HTTP 429 without Retry-After backs off at least 60 seconds. The backend scheduler supplies bounded retention when enabled; operators must still configure backups and inspect disk/job outcomes.

`GET /api/v1/stations/nearby?lat=40.748&lon=-73.985&radius=1000&limit=20` uses indexed PostGIS `ST_DWithin`, ranks distance × 1.3 as a walking approximation, and returns inventory/operating flags. Not pedestrian routing. Data older than five minutes, future-dated or absent is stale; missing inventory stays null, never zero. Synthetic stations require `include_demo=true` outside the explicitly isolated `DEMO_MODE` application.

## Integrated offline app

For a usable isolated app rather than ingestion-only error fixtures, run `docker compose -f compose.demo.yml up --build --wait --wait-timeout 180`, open localhost:8080, and choose Replay. It deterministically seeds two clearly synthetic stations and February 28, 2025 replay counts, retains provenance, and is idempotent. Accounts/commutes persist in its own PostgreSQL volume. No streets are fetched; no synthetic inventory triggers real alerts/risk. The seed refuses databases containing real stations. See [final verification](FINAL_VALIDATION.md).

The trusted native seed is `DEMO_MODE=true BACKGROUND_JOBS_ENABLED=false python -m app.modules.ingestion.demo` with a separately migrated demo database (PowerShell uses `$env:` variables). The older CLI `seed-demo` below intentionally tests duplicate/bad-record ingestion; it is not the integrated app seed.

## Ingestion fixtures and tests

```powershell
docker compose exec backend python -m app.modules.ingestion.cli --data-dir data/demo seed-demo --publish
docker compose exec backend python -m app.modules.ingestion.cli --data-dir data/demo aggregate --start 2025-01-01 --end 2025-01-02
docker compose exec backend pytest
```

Fixtures contain two `DEMO-` stations, duplicate rides and deliberate invalid records. Demo snapshots are timestamped in January 2025, so they correctly appear stale now. `is_demo` is persisted and returned; synthetic values do not masquerade as measurements. Keep demo and real staging directories separate. Unit/integration tests use deterministic local CSV/JSON and mock HTTP, never internet access. The PostGIS contract test runs when `APP_ENV=test` and `DATABASE_URL` points to an isolated migrated test database; CI already supplies both. It checks publication, duplicate inventory, spatial ordering, stale inventory, demand totals and repeat publication. Local environments without PostGIS report it as skipped.
