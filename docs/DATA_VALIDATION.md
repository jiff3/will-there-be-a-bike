# Real-data validation record

Executed on Windows on October 9, 2026 (Pacific time). This is a record of actual execution, not a claim that the unavailable Docker/PostGIS deployment was exercised.

## Historical import

The saved current GBFS catalog selects 100 stations nearest Midtown, with provider `short_name` aliases linking trip station IDs to canonical GBFS IDs. The exact selection and alias mapping are retained in each run's scope in `data/real/stage.sqlite3`; generated operator data is Git-ignored. A future live catalog selection may differ.

| Official archive | Source rows scanned | Invalid records excluded | Observed demand bins | Selected departures | Selected arrivals |
| --- | ---: | ---: | ---: | ---: | ---: |
| January 2025 | 2,124,475 | 4,806 | 173,721 | 308,664 | 311,199 |
| February 2025 | 2,031,257 | 5,309 | 157,095 | 303,104 | 301,510 |

There are 950,604 unique valid trips touching the selected stations. The two source-row totals include the whole city archives; only scope-matching trips are retained. Exclusion counters include missing station IDs (4,512 / 4,992), invalid durations (288 / 315), and invalid coordinates (6 / 2). Accepted duration is 60 seconds through 24 hours, an explicit quality policy, not proof that longer observed rides never occurred.

Source SHA-256 checksums:

- January: `ed3b0138903960afe50386ab44fec8bf52d69474b26f5c0af83e648094aea096`
- February: `e4860762fb07561bbda251a20223d996e39c6c2c8bc2592896507bfbaa40ce78`

Downloads recovered from an interrupted transfer. Imports were also interrupted and resumed from committed member/row checkpoints. Repeating the completed January command left the unique trip count unchanged. Both ZIPs were removed by the pipeline after complete ingestion/export; normalized trips, checksums, provenance and aggregates remain. The ZIPs can be downloaded again from the documented official source.

`scripts/verify_data.py` independently bucketed retained trip endpoints using SQLite SQL, compared every exported bin in order, reconciled both endpoint totals, and checked source checkpoint/exclusion totals. All assertions passed. SQLite `PRAGMA quick_check` returned `ok`.

The dense export contains 566,400 rows: 100 stations × 59 days × 96 UTC bins. Departure coverage is complete; 9,600 arrival bins are intentionally uncovered during the first 24 hours because preceding-month carry-in trips were not imported. Adjacent January/February coverage merges correctly. No exported rows are synthetic. Demand totals are observed rides, **not historical station inventory or stockout labels**.

The retained real-data directory measured 695,996,264 bytes after raw ZIP cleanup, approximately 664 MiB. The importer was observed at about 281 MiB working memory during February processing. These are local observations, not a benchmark of the unexecuted composed stack.

## Live feed

Official GBFS discovery and real status parsing succeeded. Each checked payload contained 2,519 station records with zero schema rejections. Initial diagnostic collections retained full-feed observations; the final scoped collection retained all 100 configured stations, with zero missing selected stations. Subsequent collections retain only this catalog scope. Publisher/report/fetch timestamps are preserved and stale data is flagged, not replaced by zero.

## Commands executed

The host has Python 3.10, not the project's deployment target of Python 3.12. These commands ran using an isolated temporary Python environment containing the backend dependencies. Production and CI still target Python 3.12.

From `backend/`, with `python` representing that environment:

```powershell
python -m app.modules.ingestion.cli --data-dir ../data/real stations --limit 100
python -m app.modules.ingestion.cli --data-dir ../data/real backfill --start 2025-01-01 --end 2025-02-01 --keep-raw
python -m app.modules.ingestion.cli --data-dir ../data/real backfill --start 2025-02-01 --end 2025-03-01 --chunk-size 20000
python -m app.modules.ingestion.cli --data-dir ../data/real aggregate --start 2025-01-01 --end 2025-03-01
python -m app.modules.ingestion.cli --data-dir ../data/real validate --start 2025-01-01 --end 2025-03-01
python scripts/verify_data.py ../data/real
python -m app.modules.ingestion.cli --data-dir ../data/real backfill --start 2025-01-01 --end 2025-02-01
python -m app.modules.ingestion.cli --data-dir ../data/real collect
python -m app.modules.ingestion.cli --data-dir ../data/demo seed-demo
python -m app.modules.ingestion.cli --data-dir ../data/demo aggregate --start 2025-01-01 --end 2025-01-02
python -m app.modules.ingestion.cli --data-dir ../data/demo validate --start 2025-01-01 --end 2025-01-02
python -m pytest -ra
python -m mypy app
python -m ruff check .
python -m alembic upgrade head --sql
```

Profiling also used partial `--max-rows` imports; they were resumed to completion and never published as complete datasets. Final automated checks: **23 passed, 1 skipped**; Ruff clean; strict mypy clean across 27 source files. Compose YAML parsed successfully. Native ASGI requests returned liveness 200 and dependency readiness 503 with `database: unavailable`; the DNS/connection readiness regression was fixed and tested.

## Explicit remaining verification

This section records the earlier phase's environment limitation. The later backend phase successfully provisioned local PostgreSQL/PostGIS through WSL, published the real checkpoints and exercised database-backed HTTP endpoints; see [the subsequent execution record](BACKEND_VALIDATION.md). Docker images remain unexecuted locally.

Docker Desktop/PostgreSQL/PostGIS is unavailable on this host. Migration SQL rendered successfully, but applying it, PostgreSQL publication, database-backed spatial queries, and the composed application's readiness were **not executed locally**. The isolated PostGIS test is skipped locally and enabled by the existing CI database job; no remote CI run is claimed.

Run the database-backed commands in [data operations](DATA_INGESTION.md) on a Docker-capable host, then run the PostGIS integration test against a migrated isolated test database. Snapshot retention/backup policy, publication-aware historical inputs, calibrated stockout outcomes and the frontend station-data workflow remain subsequent stages. Historical trip counts cannot supply missing historical inventory labels.

## Executed forecasting workflow

The subsequent ML phase trained eight CPU LightGBM candidates and evaluated seasonal/recent-period baselines on this real processed import. Final artifact `v1-888b8997541c0ad2` used demand SHA-256 `7d866d3678b0469585a4865619a4744fb7af07ade7e0e5ebd635b39d9f306447`. Pre-training reference coordinates were recovered for 91 stations from the January source archive with the same verified archive checksum above; historical capacity remained null. The temporary re-downloaded ZIP was removed after its derived metadata and checksum were saved; it can be downloaded again from the official source.

Initial runs added provenance checks and complete workflow memory accounting without changing fixed seeds, splits or model parameters. A final leakage audit then corrected seasonal warm-up eligibility to respect the completed-count release delay, and prohibited replay before selection/calibration completion. The full workflow was rerun with unchanged parameters and selection rules; this was a correctness fix, not test-period tuning. The final workflow, including report rendering, took **59.00 seconds**, with a measured peak process footprint of **185,475,072 bytes** (about **177 MiB**) and two CPU threads. Its sealed artifact is approximately 5 MB. Early-validation selection retained the seasonal baseline for all four targets; [the measured report](ml-evaluation/REPORT.md) includes the stronger final-period LightGBM results without substituting them after observing the test period.

From `backend/`, using the same isolated native executable as the ingestion validation, the following commands were executed successfully:

```powershell
python -m app.modules.ml.cli geography --catalog ../data/real/stations.json --output ../data/ml/locations.json
python -m app.modules.ml.cli train --data ../data/real/features.jsonl --locations ../data/ml/locations.json --config ml-config.json --output app/modules/ml/artifacts --report ../docs/ml-evaluation
python -m app.modules.ml.cli evaluate --artifact app/modules/ml/artifacts/current.json --data ../data/real/features.jsonl --report ../data/ml/evaluate
python -m app.modules.ml.cli backtest --artifact app/modules/ml/artifacts/current.json --data ../data/real/features.jsonl --report ../data/ml/backtest
python -m app.modules.ml.cli train --data ../data/real/features.jsonl --locations ../data/ml/locations.json --config ml-config.json --output app/modules/ml/artifacts --report ../data/ml/repeat-report
python -m app.modules.ml.cli predict --artifact app/modules/ml/artifacts/current.json --station 05c5e17c-7aa9-49b7-9da3-9db4858ec1fc --at 2025-02-25T08:00:00-05:00 --replay
python -m app.modules.ml.cli predict --artifact app/modules/ml/artifacts/current.json --station 05c5e17c-7aa9-49b7-9da3-9db4858ec1fc --at 2025-02-25T08:00:00-05:00 --history ../data/real/features.jsonl --replay
python -m app.modules.ingestion.cli --data-dir ../data/real collect
python scripts/verify_ml.py
python -m pytest
python -m mypy app
python -m ruff check .
python -m pip wheel --ignore-requires-python --no-deps . --wheel-dir ../data/ml/wheels
```

An independent repeat training run produced the same content-addressed version, including all eight native model checksums. Evaluation/backtest/repeat-training replays match test metrics, interval results, all per-station/demand-cohort metrics and daily comparisons exactly. `data/ml/verification.json` records the reconciliation and a real-inventory diagnostic. Obsolete development artifacts were moved to Git-ignored `data/ml/archived-artifacts` for local recovery rather than shipped in the application package; only the approximately 5 MB active version remains packaged. The new live poll parsed 2,519 stations, retained 100 selected stations, and rejected none. The winter artifact correctly returned an unavailable risk estimate for the present-day low-confidence forecast; no actual stockout outcome was fabricated.

Final native backend checks: **40 passed, 1 skipped**, Ruff clean, strict mypy clean across 40 source files. Tests include offline feature parity/leakage, missing coverage, DST, count metrics, baseline rejection, native-model inference/explanations, schema/path/checksum/runtime guards, uncertainty, bounded scenario simulation and API contracts. The API tests exposed and fixed a validation-error serialization failure. Package wheel creation verified that model files and the active pointer are included. Compose YAML parsed successfully; Docker image builds, live PostGIS inventory queries and Python 3.12 runtime execution remain unverified on this host. CI is configured to install the pinned ML dependencies and run its existing migrated PostGIS test; no remote CI run is claimed.

See [the regeneration and model limitations runbook](ML.md). The final-period forecast evaluation is an event-time archive replay, not a historical public-feed deployment; timely lag counts require a provider that actually publishes them. Empirical interval coverage is measured for the winter holdout only, and inventory scenario probabilities remain explicitly uncalibrated.
