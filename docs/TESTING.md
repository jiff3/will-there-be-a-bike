# Testing and reliability engineering

The subsequent final release pass is recorded in [FINAL_VALIDATION.md](FINAL_VALIDATION.md): 172 backend tests (81.76% coverage), 44 frontend tests, production-settings local HTTPS browser workflows, real-data retraining and provider-schema checks. Original QA/load measurements below remain dated records, not newly claimed hosted/container results.

This suite tests the existing modular monolith, not a second implementation of it. All QA fixtures are synthetic and isolated from application data. Provider requests are simulated; actual PostgreSQL/PostGIS, ingestion, SQL transactions, Python inference, cookie authentication and browser rendering are exercised.

## Test layers and safety

- **pytest:** feature/schema invariants, chronological leakage prevention, baseline/model selection, native artifact serialization, count intervals, inventory-risk qualifications, recommendation scoring/fallbacks, geospatial boundaries, request errors, freshness and job lifecycle. Small synthetic training experiments test reproducibility; their metrics are not real-data model-quality claims.
- **PostGIS integration:** migrations, spatial inventory queries, ingestion provenance and duplicate prevention, partial-write rollback, session/CSRF/rate-limit behavior, cross-user CRUD isolation, notification cooldown/concurrent deduplication, atomic replay publication, stored retrospective comparisons, durable forecast cache and advisory-lock jobs. These tests run against PostgreSQL, not SQLite substitutes or mocked repositories.
- **Vitest/Testing Library:** chart/empty-state rendering, account/commute/notification/replay controls, API typing/error contracts, request cancellation, viewport pagination, visibility/network changes and non-overlapping polling. Fake clocks replace polling sleeps.
- **Playwright:** two isolated UI-contract tests plus six full-stack journeys. The latter register/login/logout, reload authenticated preferences, select actual MapLibre station layers, render real server forecasts/history, request ranked recommendations, create/edit/delete commutes, create and actually evaluate alerts, persist notification read state, deny a second authenticated user access, play/pause/speed/bound replay, and exercise mobile, offline, stale-feed and empty-data states. Only the basemap is replaced by a tile-free local style; API responses are not mocked.

Use an **isolated database ending in `_test`**, `APP_ENV=test`, and `BACKGROUND_JOBS_ENABLED=false`. pytest's `--require-postgis` refuses unsafe configuration instead of silently skipping the database suite. Fixture commands guard both CLI and direct helper entry points before connecting. Integration tests and the browser fixture driver mutate test tables: never run them on the real-data database, and do not run browser seeding concurrently with pytest.

pytest blocks unexpected outbound HTTPX traffic; ASGI and explicit MockTransport requests remain allowed. Full-stack browser tests block non-loopback requests. Dependency installation/container pulls still require network access; no test requires Citi Bike, a basemap provider, production credentials, email or SMS.

The QA seed publishes 100 synthetic station locations/statuses, 480 generated trips through ZIP/chunked staging and the actual provenance/aggregation pipeline, and 192 replay bins for two stations on **2025-02-28, New York time**. Two fixture IDs match the sealed inference artifact's station identities solely to exercise serving. They are not new official measurements. The fixture author explicitly declares coverage for the finite generated trip universe; unknown real sources still cannot assert coverage. No historical inventory is invented. The currently deployed winter artifact correctly qualifies present-day demand/risk as low confidence.

## Reproducing the suite

Requirements: Python 3.12, Node 22.12+ (or supported Node 20.19+), Chromium installed by Playwright, and PostgreSQL 16/PostGIS. Prefer a separate Docker QA stack when Docker Desktop is available. Run one browser worker and one pytest process; training is bounded and separate from serving. The QA containers cap database/API/nginx memory at 768 MiB/1 GiB/128 MiB respectively. Build processes, browser and test runner need additional memory; do not run all layers/training concurrently on an 8 GB machine.

Install dependencies from the repository root:

```bash
python -m venv .venv
# POSIX: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -c backend/constraints.txt -e "backend[dev,ml,performance]"
npm --prefix frontend ci
cd frontend
npx playwright install chromium
cd ..
```

The Python constraints snapshot records the tested Python 3.12 dependency versions. npm uses its committed lockfile. Platform-only wheels and container base tags are not hash-locked: this is constrained dependency resolution, not a claim of bit-for-bit universal reproducibility.

Start the disposable database (port 5432 must be available):

```bash
npm run qa:build
npm run qa:db
```

Set environment variables in the shell that runs migrations/tests. PowerShell:

```powershell
$env:APP_ENV='test'
$env:DATABASE_URL='postgresql+asyncpg://bike_qa:disposable-local-test-only@127.0.0.1:5432/bike_qa_test'
$env:BACKGROUND_JOBS_ENABLED='false'
$env:MPLBACKEND='Agg'
$env:OMP_NUM_THREADS='1'
$env:AUTH_RATE_LIMIT='100'
```

POSIX equivalents:

```bash
export APP_ENV=test
export DATABASE_URL='postgresql+asyncpg://bike_qa:disposable-local-test-only@127.0.0.1:5432/bike_qa_test'
export BACKGROUND_JOBS_ENABLED=false MPLBACKEND=Agg OMP_NUM_THREADS=1 AUTH_RATE_LIMIT=100
```

From `backend/`:

```bash
python -m alembic upgrade head
python -m alembic check
python -m ruff check .
python -m mypy app
python -m pytest --require-postgis --cov=app --cov-report=xml --cov-report=html --cov-report=term --junitxml=reports/junit.xml
python -m tests.support.qa seed
```

CI also executes `alembic downgrade base` followed by `upgrade head` and `check` **before inserting data**. Full downgrade drops application tables and the PostGIS extension; use an empty disposable database with a dedicated migration credential able to manage extensions. It is not an operational rollback plan and must never target a shared/live database.

From `frontend/`:

```bash
npm run lint
npm run build
npm run format:check
npm run test:run
npx playwright test smoke.spec.ts
```

For persisted E2E, start the migrated/seeded stack from the root:

```bash
npm run qa:up
```

In addition to the database environment above, set:

```powershell
$env:BIKE_E2E_QA='1'
$env:BIKE_E2E_EXTERNAL='1'
$env:BIKE_E2E_BASE_URL='http://localhost:8080'
$env:BIKE_E2E_PYTHON=(Resolve-Path .venv/Scripts/python.exe).Path
cd frontend
npx playwright test fullstack.spec.ts
```

POSIX uses `export` and an absolute `BIKE_E2E_PYTHON` pointing to `.venv/bin/python`. Full-stack tests invoke guarded Python commands to refresh/stale observations or execute the real alert job; there are no unauthenticated HTTP test-control endpoints. Test-owned browser accounts are cleaned before/after the suite. The database's temporary data is removed when `npm run qa:down` stops the tmpfs-backed QA stack; do not substitute the normal production/development Compose project.

### Native Windows/WSL alternative

If Docker is unavailable, provision the same isolated role/database in an existing local PostgreSQL/PostGIS installation, migrate, run pytest, and seed as above. For browser checks start the API with the same test settings, `uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log`. Leave `BIKE_E2E_EXTERNAL` unset, set `BIKE_E2E_BASE_URL=http://localhost:5173` and `VITE_MAP_STYLE_URL=/qa-map-style.json`; Playwright starts Vite and its API proxy. Then run `npx playwright test` from `frontend/`.

Use **127.0.0.1 in the database DSN** on the validated Windows-to-WSL setup. `localhost` tried an unavailable IPv6 address first and added approximately two seconds, falsely tripping the bounded readiness/inventory timeouts. This is a local connection configuration issue, not justification for weakening timeouts.

Four older browser checks in `real-backend.spec.ts`/`rider-real.spec.ts` require `BIKE_E2E_REAL=1` and the populated original real-data API; they are intentionally not part of the offline fixture job. They were skipped in this QA pass. Do not enable their real-data mode against the synthetic QA database.

## Failure injection and fixes

- GBFS connect errors/read timeouts retry a bounded 1/2/4-second schedule, then surface failure; tests capture backoff without waiting. Permanent 404s are not retried; long Retry-After values become recorded scheduling delays, not hour-long worker sleeps.
- Malformed discovery, incomplete payloads, non-boolean operational flags, boolean timestamps and station reports later than their feed envelope are rejected. Future-dated observations cannot displace the latest valid inventory. Duplicate and older reports preserve indexed latest-observation semantics and audit counts.
- A feed with no usable observations now records a failed background job instead of falsely reporting successful collection. The prior valid inventory remains intact and a subsequent valid run recovers.
- Interrupted PostgreSQL connections roll back uncommitted writes; a subsequent connection recovers. A real refused-port engine verifies sanitized 503 responses instead of exception/credential disclosure. Partial station publication rolls back atomically.
- Interrupted/cancelled jobs release advisory locks, record failure/abandonment appropriately and allow a subsequent run. Shutdown now awaits cancellation before disposing the database engine.
- Missing/invalid native model artifacts do not prevent non-ML application startup. Native inference errors return a consistent unavailable-model error; recommendations retain qualified inventory/distance fallback. Partial failed results are not cached.
- Count-cache tests verify station/version/issuance/expiry keys, bounded eviction and defensive copies. Inventory and risk are recomputed from observations even after a count-cache hit.
- Sparse/incomplete trip coverage remains missing rather than zero. CLI completeness validation returns nonzero for gaps; resumed chunked ingestion and duplicate imports do not inflate counts. Day/week lags, delayed release times and historical geography tests reject target/future-capacity leakage.
- Extreme replay dates now return validation errors instead of overflowing the next-day boundary. Browser pagination rejects non-advancing offsets and stops after ten pages, including sparse-filter cases.
- Client cancellation, 15-second deadlines, hidden-tab polling, offline recovery and single-flight refresh are covered with fake clocks. Browser logout assertions now await the real logout response; replay assertions wait on state, not arbitrary animation sleeps. Retries are disabled so a failed first attempt cannot be hidden by an automatic rerun.

## Measured results

Executed locally on **2026-10-10** using Windows, Python 3.12, Node 24, Chromium, and real WSL PostgreSQL 16.9/PostGIS 3.5.3. The host has an i7-6700 (4 cores/8 threads) and **15.94 GiB RAM**; it is not an actual 8 GB-host qualification. Tests were run sequentially against isolated test databases, preserving the original real-data database.

- Backend: 145 tests passed in 38.07 seconds, no skipped database tests; application coverage 82.97%. Coverage gate: 75%. Ruff and strict mypy passed (68 application source files). Coverage reports retain gaps rather than hiding them: several operational CLI wrappers and repository failure branches need additional targeted coverage; they are not excluded to inflate the total.
- Frontend: 42 tests passed; lines 78.26%, statements 77.18%, branches 71.46%, functions 66.45%. Gates: 75/75/65/60 respectively. ESLint, TypeScript/Vite production build and Prettier checks passed.
- Chromium: 8 passed (six persisted full-stack journeys and two isolated smoke tests), four real-data opt-in checks skipped. A subsequent `npx playwright test --repeat-each=2` also passed all 16 executed cases, with eight opt-in skips, in approximately two minutes. No retries. Desktop and 390-pixel mobile screenshots were captured and inspected.
- Dedicated empty migration database: actual upgrade → full downgrade → re-upgrade → schema-drift check passed. Temporary migration-role superuser access was revoked afterward.

### Repeatable load benchmark

From `backend/`, with a seeded QA API running:

```bash
python performance/benchmark.py --host http://127.0.0.1:8000 --users 8 --seconds 30 --server-pid API_PID --output reports/load
```

`--server-pid` is optional and must identify your API process. The driver only accepts loopback hosts, limits concurrency to 16 and duration to 120 seconds, and writes Locust HTML/CSV, console output, final statistics with histograms, summary JSON and optional 0.5-second RSS/CPU samples. The final event captures completed requests rather than relying on an incomplete periodic CSV flush. It fails on any HTTP errors or aggregate p95 above a deliberately generous 2-second QA smoke budget; this is not a production SLO. Reference: [Locust headless execution](https://docs.locust.io/en/2.35.0/running-without-web-ui.html).

For comparable native performance measurements, use **APP_ENV=development** on the same disposable QA database with provider jobs disabled: this enables the real 4+2 connection pool. Tests use NullPool for event-loop isolation; benchmarking that configuration is not representative of production pooling. Refresh QA observations before the run using the guarded driver under `APP_ENV=test`, and do not run ingestion/training/browser tests concurrently with the load benchmark.

The measured workload uses 100 synthetic stations and the actual sealed inference artifact, one API worker, eight users, 30 requested seconds, 50–150 ms think time, and weighted bbox/nearby/inference/recommendation requests (3/2/2/1). These are predominantly warm count-cache reads; no new real-data training-quality claim is made. Provider updates were disabled. Latency percentiles are Locust histogram approximations.

| Endpoint | Pooled baseline p95 ms | Optimized p50/p95/p99 ms | Optimized repeat p95 ms |
| --- | ---: | ---: | ---: |
| Bounding-box stations | 72 | 12 / 30 / 110 | 26 |
| Nearby stations | 91 | 9 / 31 / 210 | 25 |
| Demand inference | 79 | 14 / 41 / 92 | 32 |
| Recommendations | 730 | 26 / 59 / 340 | 49 |
| Aggregate | 510 | 13 / 41 / 150 | 34 |

Baseline: **1,174 requests, 39.58 req/s, zero failures**. Optimized: **1,808 requests, 61.77 req/s, zero failures**. Independent optimized repeat: **1,982 requests, 67.39 req/s, zero failures**. API peak RSS was 139.50 / 137.71 / 136.08 MiB respectively; sampled mean CPU was 78.15 / 43.99 / 46.34% of one core. RSS/CPU cover **only the API PID**, not PostgreSQL, WSL, Chromium or the load generator. Small local runs with ordinary OS scheduling variation are evidence for this bottleneck, not statistically rigorous production capacity tests. A compact durable record is in [qa-results.json](qa-results.json).

Profiling exposed repeated database count-cache reads across recommendation candidates. The fix is a 512-entry defensive-copy LRU keyed by station/model/issuance with expiry; the durable database cache remains the cold/process-independent layer. Model loading is warmed in a thread at startup. No risk/inventory cache, queue, extra service or larger connection pool was introduced. An earlier exploratory NullPool/concurrent-tools run exceeded the budget and is not used as a fair before/after comparison.

## CI, artifacts and deployment

`.github/workflows/ci.yml` has four bounded jobs on PRs/main and as a reusable workflow:

1. Python lint/types, empty PostGIS migration roundtrip, all unit/integration tests and coverage/JUnit/HTML reports.
2. Frontend lint/types/production build/format/unit coverage, plus isolated offline Chromium smoke checks.
3. Build the actual production Python/nginx images; start ephemeral PostGIS; migrate/seed; boot the runtime; execute six persisted Chromium journeys; run a four-user/15-second HTTP load smoke; collect container logs, status, memory snapshots and browser/load artifacts even on failure. This job tests production **image code**, with explicitly insecure test-only HTTP cookie settings, not a TLS deployment.
4. Build the root managed-service production image, migrate/seed an isolated offline database, and run both actual-app walkthrough/account journeys. Collect video/browser/container diagnostics and stop this job's own stack. This is container execution on CI, not a claimed local Docker run.

The Render workflow is manual/owner-enabled and reruns all four jobs before provider release; it verifies commit/model identity without forwarding provider credentials to public smoke endpoints. See [deployment](DEPLOYMENT.md).

Generated local artifacts are ignored: `backend/reports/`, `backend/coverage.xml`, `backend/htmlcov/`, `frontend/coverage/`, `frontend/test-results/`, and `frontend/playwright-report/`. Use `npx playwright show-report` for screenshots/failure reports or `npx playwright show-trace PATH_TO_TRACE.zip` for a failing trace. See [Playwright trace guidance](https://playwright.dev/docs/trace-viewer). Protect downloaded artifacts because browser traces can contain test-session data. Test secrets are disposable; production values are never needed.

`.github/workflows/deploy.yml` is manual and disabled unless repository variable `DEPLOY_ENABLED=true` and the selected branch is main. It reruns CI before publishing immutable commit-SHA GHCR images. Configure a protected `production` environment, approved deployment host/user, SSH key and pinned known-hosts secrets (`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`). The server must already have Docker Compose, registry access, a clean checkout at `/opt/will-there-be-a-bike`, production environment configuration/TLS ingress, migration privileges and verified backups. The release pulls images, migrates once, then checks readiness. Database migrations are not automatically reversed on failure; backward-compatible migrations and operator rollback/restore plans remain required. No remote deployment was performed.

### Explicit verification limits

Docker is not installed on the validation host. Production image builds, container-based browser/load runs and GitHub execution remain unexecuted. Local production-settings HTTPS was subsequently exercised, but no hosted deployment occurred. Workflow/Compose parsing and provider-schema validation are not passing GitHub runs. CI must fail on unresolved platform differences.

Only Chromium was run; Safari/Firefox, broad accessibility/security audits, long-duration soak tests, backup/restore drills and deployment interruption tests remain follow-up work. No independently calibrated stockout outcomes or historical inventory are claimed. Real-data browser comparisons remain opt-in and were not rerun in this phase; stored retrospective forecast comparisons are covered by PostGIS/API and component tests.
