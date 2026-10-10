# Saved commutes, in-app alerts and demand replay

These features extend the existing modular monolith, station data, approved model and React design. No new infrastructure, forecasting implementation in JavaScript, external messaging service or synthetic city animation is introduced.

## Run and use

Apply the additive migration before starting an older checkout:

```powershell
docker compose run --rm backend alembic upgrade head
docker compose up --build
```

The required revision is `0005_rider_features`. Compose enables `BACKGROUND_JOBS_ENABLED=true`. Native development uses the existing PostgreSQL `DATABASE_URL`, `alembic upgrade head`, `uvicorn app.main:app`, and `npm run dev` from `frontend`; enable the same scheduler setting in the API environment. Production security settings and same-origin cookies remain unchanged; see [SECURITY.md](SECURITY.md).

- **Saved commutes:** register/sign in, open Saved commutes, choose station landmarks or approximate coordinates, set local time/time zone, ISO weekdays and walking limit. Create, edit, delete and reload persist in PostgreSQL. The map planner can save its selected endpoints. Each card can request current recommendations from the existing backend ranker, or reopen its points/preferences in the map planner. Immediate checks explicitly use departure **now**, not a fictitious multi-day forecast for the next scheduled commute.
- **Availability alerts:** open a commute's Availability alerts, select an actual station, choose low pickup bikes or low destination docks, a measured count or estimated-scenario threshold, a 30/60-minute pre-departure window, and cooldown. Edit, pause/enable and remove all persist. Notifications appear in the authenticated inbox with read/unread state and a navigation badge; the client polls serially every minute while visible/online. There are no email, SMS, browser-push or operating-system notifications.
- **Replay:** choose a published New York date and departures/arrivals, drag or keyboard-adjust the quarter-hour slider, play/pause, or change speed. Select stations in the map or accessible searchable list to see observed directional ride counts and stored comparisons. Playback stops at the final frame and pauses when the tab is hidden. Each date is fetched once; animation changes a bounded local frame, not a request stream.

## API contracts

All paths below are relative to `/api/v1`; OpenAPI includes these endpoints. Private requests use the existing HttpOnly session. Mutations require exact allowed Origin plus the session-bound `X-CSRF-Token`; frontend CSRF tokens stay in memory. Ownership comes exclusively from the authenticated user, never a supplied user ID. Foreign saved IDs return 404. Private responses carry `Cache-Control: no-store`.

| Method/path | Behavior |
| --- | --- |
| `GET/POST /commutes` | List owned commutes / create; at most 20 per account. |
| `GET/PUT/DELETE /commutes/{uuid}` | Read, replace settings or delete owned commute. |
| `GET /commutes/{uuid}/recommendations` | Existing station/model ranker, saved points/preferences, departure now. |
| `GET/POST /commutes/{uuid}/alerts` | Owned rules / create; at most four per commute. |
| `PUT/DELETE /commutes/{uuid}/alerts/{rule_uuid}` | Edit/pause/enable/remove owned rule. |
| `GET /notifications?limit=20&offset=0` | Owned history, unread count and next offset; limit ≤50. |
| `PATCH /notifications/{uuid}` | `{"read":true}` or `{"read":false}`. |
| `GET /replay/catalog` | Active export provenance, supported local dates; honest empty response without a publication. |
| `GET /replay/day?day=2025-02-28` | Station metadata, UTC quarter-hour timestamps and directional nullable count arrays. |
| `GET /replay/stations/{external_id}/comparisons?day=2025-02-28` | Stored model version/computation time, means/intervals, subsequent observed cumulative counts. |

Commute body example (station IDs are optional landmark references, not identity-based name matching):

```json
{
  "name": "The morning ride",
  "origin": {"label":"W 35 St & 9 Ave", "latitude":40.754, "longitude":-73.997},
  "destination": {"label":"Madison Ave & E 26 St", "latitude":40.743, "longitude":-73.987},
  "departure_local":"08:30", "timezone":"America/New_York", "weekdays":[1,2,3,4,5],
  "preferences":{"max_walk_meters":700,"reliability_weight":0.7}
}
```

Coordinates above are approximate illustrative places, not a route. Walking distances remain great-circle distance ×1.3, not verified pedestrian routing.

## Alert semantics and operations

The lightweight scheduler evaluates up to **200 enabled rules per minute**, oldest checked first, under a cross-worker PostgreSQL advisory lock. It records job outcomes in `operation_jobs` and rule-specific last-checked/status. More than 200 rules create evaluation lag, not extra processes; monitor that lag before expanding deployment scope. A failed network/DB dependency is recorded, never converted to a positive alert or an all-clear.

Freshness requires all three station timestamps (reported, feed updated, fetched) within five minutes, non-demo measurements and the appropriate renting/returning/install flags. Closed, missing or stale data does not trigger an availability condition. Measured count conditions use `count <= threshold`; estimated conditions use `scenario boundary percentage >= threshold`. Estimated alerts require a recent, non-low-confidence server forecast and an available bounded inventory scenario. They explicitly state **uncalibrated**, not a validated real stockout probability. The approved winter artifact is low-confidence in October 2026, so estimated-risk alerts correctly remain silent there; measured-inventory alerts still work.

The forecast issuance window is not exactly the commute interval. Destination checks use the same pre-departure window, **not** a routed arrival time. Silent notifications are not a guarantee of availability. These limits are disclosed beside rule settings and empty history.

Notification writes lock/recheck the parent settings and rule, enforce cooldown (15–1440 minutes) and a database uniqueness constraint on `(rule_id, scheduled_for)`. A rule can notify at most once per departure occurrence, even after cooldown expires. Recreating a rule is a new rule. Spring-forward nonexistent departure times are skipped; repeated fall-back times use only the first occurrence. Deleting a commute cascades its rules; notification rule references become null and owned history survives until the **90-day** bounded hourly retention job. Deleting an account at the database lifecycle boundary cascades its private data.

Trusted operator command, respecting distributed locks and persisted due times:

```powershell
docker compose exec backend python -m app.modules.monitoring.cli alerts
```

This is not exposed as an HTTP job-execution endpoint.

## Publish real replay and retrospective comparisons

Use a coverage-aware export produced by the existing ingestion pipeline, not raw trip-count sums. See [DATA_INGESTION.md](DATA_INGESTION.md) and [ML.md](ML.md) for generating `features.jsonl`. Native commands from `backend`:

```powershell
python -m app.modules.replay.cli publish --data ../data/real/features.jsonl
python -m app.modules.replay.cli backtest --day 2025-02-28 --stations 05c5e17c-7aa9-49b7-9da3-9db4858ec1fc 06109e92-4363-445a-9743-0457f6799530
```

Compose equivalent, with the existing data directory mounted as `/app/data`:

```powershell
docker compose exec backend python -m app.modules.replay.cli publish --data /app/data/real/features.jsonl
docker compose exec backend python -m app.modules.replay.cli backtest --day 2025-02-28 --stations 05c5e17c-7aa9-49b7-9da3-9db4858ec1fc 06109e92-4363-445a-9743-0457f6799530
```

Publication validates and hashes the file in a streaming preflight, then inserts batches of 500 in one transaction and rechecks the checksum before activation. Duplicate bins or changed files roll back; the previous active publication remains available. Repeating the identical checksum reuses it. Scope is capped at 120 stations, 93 days and 1.1 million bins. Only real-catalog station identities and non-demo exports are accepted. Partial counts with unverified directional coverage are suppressed to **null**, never zero-filled. Retained inactive publications are operator-managed; review/export them before removal and monitor disk usage when publishing revisions.

Daily queries are bounded to 12,000 rows and use indexed import/time selection. Dates have 92, 96 or 100 UTC frames according to New York DST. Current station geography is explicitly labeled; no historical location or bike inventory is reconstructed. Counts describe observed rides, not unmet demand.

Backtests use the existing loaded, sealed artifact with archive-only calendar/training-reference features and **no current inventory or target ride counts supplied as inputs**. The active export checksum must match the evaluated artifact; dates must fall entirely in its final held-out period. Each invocation admits 1–10 supported stations. Forecasts retain artifact version and actual computation time and are clearly retrospective—not recorded live. Comparison outcomes require complete directional coverage across the subsequent 30/60-minute windows, otherwise null. The UI never generates a missing forecast during playback.

## Actual verification (2026-10-10)

On native Windows Python 3.12 / Node 24 with WSL PostgreSQL 16 + PostGIS, migration upgrade and metadata checks passed. Published the existing **real January–February 2025** processed export: **566,400 bins / 100 stations / 59 dates**, SHA-256 `7d866d3678b0469585a4865619a4744fb7af07ade7e0e5ebd635b39d9f306447`. Stored **192** retrospective forecasts on February 28 for W 35 St & 9 Ave and Madison Ave & E 26 St with unchanged approved artifact `v1-888b8997541c0ad2`.

Executed `pytest` against PostgreSQL, `ruff check app tests`, `mypy app`, `alembic check`; frontend `npm run typecheck`, `npm run lint`, `npm run test:run`, `npm run build`, and Playwright. Opt-in real rider tests exercised actual account/cookie/CSRF flows, saved commute reload/edit/delete, Python recommendations, actual fresh-feed threshold evaluation by the scheduled job, notification history/read/unread, replay/comparison playback and 1440×960/390×844 layouts. Unit fixtures are explicitly synthetic and never imported by application pages; isolated DB fixture rows are removed in test teardown. See the test files for exact assertions.

Verification currently passes **85 backend tests and 35 frontend tests**, including authorization/failure handling, concurrency, quotas, DST boundaries, null coverage, atomic replay rollback, repeatable backtest persistence and real UI workflows. API readiness returns database `ok`; `alembic check` reports no drift. At a native serving snapshot the API working set was ~153 MiB and Vite ~180 MiB; the isolated database including the existing real trips and replay was 778 MB. These are point-in-time process/disk measurements, not total-host or load-test guarantees.

Playwright passed two deterministic offline browser tests and four opt-in real-backend workflows. A final rider rerun also verified removing a rule while preserving history and passing actual planner endpoints into the saved-commute form. Frontend lint, TypeScript, formatting and production build pass; backend lint/type checks pass. Preview processes have been stopped; persisted PostgreSQL replay data and forecast comparisons remain available on the next application start.

Browser visual review retained the established cream/green design, added unique accessible picker IDs, and gave historical demand its own green intensity scale instead of live-inventory warning colors. Map updates now explicitly remain busy until rendered, avoiding a newly selected timeline label appearing beside a screenshot of the previous frame. Desktop/mobile screenshots are saved locally under `data/validation/rider-*.png` (ignored build/QA artifacts).

Run offline/unit checks using the existing project commands. Real browser checks require the populated local PostgreSQL-backed API and Vite server:

```powershell
cd frontend
$env:BIKE_E2E_REAL='1'
npx playwright test e2e/real-backend.spec.ts e2e/rider-real.spec.ts
```

These create disposable example.com test accounts and permissive **test-owned** alert conditions against actual measurements; no fake measurement or forecast is inserted. The rider test normally waits up to 90 seconds for the scheduled alert job. `BIKE_E2E_PYTHON` plus `DATABASE_URL` optionally enables the trusted local CLI check; it still respects the persisted due time and advisory lock. Default Playwright runs only deterministic offline contracts; opt-in tests do not run against a production account/database. PostgreSQL fixture tests require `APP_ENV=test` and a migrated isolated `DATABASE_URL` and restore any prior active replay import.

Docker is unavailable on this host, so new container builds and production HTTPS behavior were not executed. Existing container architecture is preserved. No retraining, calibrated inventory claims, external notification delivery or guaranteed scheduling SLA is asserted.
