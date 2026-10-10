# Architecture

## Purpose and constraints

“Will There Be a Bike When I Get There?” predicts two related events: whether a usable bike will be present at a pickup station at departure time, and whether an open dock will be present at a destination at arrival time. It must remain useful when predictions are uncertain, data feeds lag, or a chosen station is likely to fail.

The system is a modular monolith. One API deployment owns the domain workflow, persistence, ingestion coordination, model inference, authentication, recommendations, and monitoring. Module boundaries are explicit Python packages, not network hops. This minimizes operational cost and memory use on an 8 GB Windows/Docker Desktop machine while leaving clean seams for background processes later. Model training is deliberately a separate, on-demand process and is never run inside the serving API.

## Runtime topology

```text
Browser (React + MapLibre)
       |
       | HTTPS / JSON, /api/v1
       v
nginx (production static assets + API proxy)
       |
       v
FastAPI modular monolith
  | ingestion | forecasts | recommendations | auth | commutes | replay | monitoring |
       |
       | async SQLAlchemy
       v
PostgreSQL 16 + PostGIS
```

Development exposes Vite on 5173 and FastAPI on 8000, with relative `/api` proxied by Vite. The nginx/Compose deployment exposes only nginx on 8080. The managed Render alternative uses the root multi-stage Dockerfile: FastAPI serves built React and `/api` behind provider HTTPS, with private managed PostgreSQL. Hashed assets are immutable-cacheable, UI routes fall back to index, missing APIs/assets do not. See [deployment](DEPLOYMENT.md).

Each service has a hard Compose memory ceiling. The default development total is approximately 3 GB before Docker overhead; production is capped near 4.25 GB. Workers stay at two because extra workers duplicate model memory.

## Module boundaries

- `api`: versioned HTTP composition and transport-only schemas.
- `persistence`: SQLAlchemy models, sessions, transaction helpers, and repositories.
- `ingestion`: provider adapters, GBFS polling, historical trip imports, normalization, idempotency, and data-quality checks.
- `ml`: coverage-aware demand loading, shared batch/inference features, baselines, CPU LightGBM training, chronological evaluation, native-text/JSON artifacts, uncertainty, and bounded inventory-risk simulation. Training runs in a separate process or `compose.ml.yml`, never in an API worker.
- `recommendations`: PostGIS candidate search and rankers for pickup/destination alternatives.
- `auth`: credentials or identity-provider integration, sessions/tokens, and account preferences.
- `commutes`: owned saved endpoints/schedules/preferences, availability-rule decisions and transactional notification deduplication/history.
- `replay`: sealed coverage-aware count publication, bounded daily reads and persisted retrospective held-out forecast comparisons.
- `monitoring`: feed freshness, inference telemetry, prediction outcomes, and drift metrics.
- `health`: process liveness and dependency readiness.

Modules will expose services and repository protocols. HTTP handlers should not issue arbitrary SQL or load model files directly. Cross-module calls remain in-process and are coordinated by application services.

## Data flow

### Live availability

1. A provider adapter discovers GBFS station information/status endpoints and polls with timeouts, bounded retries and TTL-aware intervals. Conditional GBFS requests remain a future bandwidth optimization.
2. Raw payload metadata is retained for traceability; validated records are normalized to canonical system, station, and observation records.
3. Idempotent writes append station-status observations. Feed freshness and station coverage are recorded.
4. A forecast request loads measured inventory known at its 15-minute issuance boundary, calendar features and the approved artifact. Public GBFS does not supply recent completed-trip counts; the serving path uses the evaluated archive-only model. Optional lag-enhanced inference requires an explicitly available completed-trip provider.
5. The API returns count forecasts, residual-based prediction intervals, model timestamps and qualified scenario risks. These are not calibrated actual stockout probabilities. Weather is not implemented. Stale inventory or an out-of-context model suppresses risk estimates.
6. Recommendations use PostGIS candidates, approximate walking cost, fresh measured bikes/docks and qualified count-derived inventory scenarios. Out-of-context/unavailable ML falls back to live inventory; missing/stale inventory yields explicitly distance-only alternatives. Ranking scores are not calibrated availability probabilities.

### Historical replay

Demand backtests use shared feature code and an event-time “as-of” boundary. Future count windows never enter features, and target windows crossing split boundaries are purged. Archive publication timestamps are unknown: the measured 2025 replay is retrospective, not proof that a public-feed deployment could have obtained those counts then. Lag-enhanced comparisons are conditional on a completed-trip provider with the configured delay. Normal inference requires an already-created artifact; explicit CLI replay is required for earlier dates. Availability-outcome replay remains planned because trips do not supply stockout labels.

## Planned schema

The initial migration created systems and spatially indexed stations. The second adds identity aliases, ingestion provenance, historical trips/demand and measured live snapshots. The third implements accounts, hashed sessions, isolated preferences, durable throttles, coordinated operation-job records and a versioned count-forecast cache. The fourth indexes timestamp retention and snapshot/feed relationships. The fifth adds private saved commutes, alert rules/history and versioned demand replay without replacing those schemas. A database model registry remains deferred; immutable deployment-approved artifacts remain authoritative.

| Area | Planned tables | Key design notes |
| --- | --- | --- |
| Station catalog | `bike_share_systems`, `stations` | Provider identifiers unique within a system; WGS84 `geography(Point, 4326)` with GiST index. |
| Live data | `availability_snapshots`, `feed_sync_runs` | Implemented append-only measurements; uniqueness by station/provider timestamp/demo flag; retention/partitioning after measured volume. |
| Trips | `ingestion_runs`, `historical_trips`, `trip_provenance`, `historical_demand` | Implemented source lineage, checksums, and import status; timestamps stored in UTC with system timezone retained. |
| Station identity | `station_aliases` | Implemented provider short-name/external-ID mapping; historical corrections require reviewed aliases, not name matching. |
| Accounts | `users`, `auth_sessions`, `user_preferences`, `request_limits`, `saved_commutes`, `availability_alerts`, `notifications` | Implemented Argon2id sessions/CSRF, account-isolated preferences/commutes, throttles and deduplicated scheduled in-app alerts. |
| Forecasting | `forecast_cache`, `operation_jobs`; later `model_versions`, `prediction_outcomes` | Implemented station/version/issuance count cache and durable job/version status. Inventory risk is never cached with counts. Registry/outcome persistence remain planned; immutable native-text/JSON directories remain authoritative. |
| Replay/quality | `replay_imports`, `replay_bins`, `replay_predictions`; later inventory outcomes/quality events | Implemented checksum-versioned atomic count publication, nullable directional coverage and explicitly retrospective held-out forecasts with actual computation times. No historical inventory. |

Ingestion now has indexed PostgreSQL tables for runs, trip provenance, canonical station aliases, measured availability snapshots and sparse historical demand. Import workspaces use SQLite for disk-backed checkpoints and streaming aggregation, then publish transactionally to PostgreSQL. Dense exports include missing-data masks. See [data operations](DATA_INGESTION.md). Native range partitioning is deferred until measured volumes warrant it; TimescaleDB is not assumed. PostGIS supports indexed nearest-station/radius queries now.

`stations.location` owns current geographic coordinates. Live status is the latest append-only availability snapshot, selected with an indexed lateral query; there is no redundant mutable inventory table that could disagree with observation history. Current catalog geography is not an as-of historical location record. See the [actual validation record](DATA_VALIDATION.md) for verified native imports and the local database execution limitation.

## ML lifecycle

1. The ingestion exporter supplies deduplicated 15-minute observed-ride counts and directional coverage masks. ML reads JSONL twice into bounded float32 arrays, refusing mixed demo/real data, duplicate rows and oversized scopes. Missing coverage stays NaN.
2. A pre-training warm-up alone fits station/weekday/hour seasonal averages, admitting only bins whose completed-count release delay has elapsed before the cutoff. Earlier valid trips supply reference locations; later GBFS capacity is never backdated. Shared features encode calendar patterns, historical demand and optional past-known daily/weekly counts. Forecast targets sum the next two or four UTC bins.
3. Four count targets each train calendar-only and lag-enhanced Poisson LightGBM candidates. Seasonal and comparable-period baselines are evaluated too. Separate chronological periods govern fitting, early-validation selection, late-validation residual calibration and final testing. No final-test fitting or retuning occurs; a one-way acceptance gate may reject a candidate in favor of its baseline.
4. Reports measure MAE, RMSE, zero-safe Poisson deviance, empirical interval coverage, each station, demand cohorts and daily fixed-model backtests. The executed winter experiment retained the seasonal baseline because it won early-validation MAE, although LightGBM was better in the final period. See [measured evaluation](ml-evaluation/REPORT.md) and [regeneration runbook](ML.md).
5. Immutable artifacts have configuration, seeds, source/code fingerprints, exact feature order, library version and file hashes. `current.json` activates a local deployment-approved version; serving caches it until process restart. No pickle/joblib loading, arbitrary model uploads or automatic retraining are supported. A database registry is deferred.
6. Residual uncertainty feeds a bounded Gamma-Poisson scenario simulation starting from measured usable bikes+docks. It estimates empty/full occupancy at the horizon and boundary risk within it. Independent flows, minute-level ordering, no rebalancing, fixed usable slots and censored observed demand limit interpretation. Inventory snapshots must eventually independently validate risk with calibration/Brier scores before claims about actual stockout probabilities.

## Reliability and security

- `/api/v1/health/live` verifies the process; `/api/v1/health/ready` verifies connectivity and the required schema revision. Optional model failure leaves non-ML station endpoints usable.
- Every response receives an `X-Request-ID`; errors have stable codes and do not leak exception details.
- Logs are structured JSON on stdout for container collection. Secrets come only from environment configuration.
- Development applies migrations at startup; production runs them once as a release job with a migration credential separate from the non-superuser runtime role.
- Provider GETs use bounded retries, timeouts, publisher backoff/TTL and explicit stale-data behavior. PostgreSQL session advisory locks plus persisted due times coordinate in-process live, forecast-refresh and retention jobs across workers; no queue is required.
- Argon2id accounts, HttpOnly secure production cookies, session-bound CSRF tokens, exact-Origin checks, durable auth throttles and server-derived user isolation are implemented together. Operator status requires an admin session. See [security configuration](SECURITY.md).
- SQL writes are explicit transactions; ambiguous failed writes are not blindly retried. Pools, statements, body sizes, spatial candidates and ranges are bounded. Structured logs include sanitized request IDs, route templates and timings, not raw inputs or credentials.
- Native model loading is warmed in a thread at startup; optional model failure does not prevent station/account service. A 512-entry process-local LRU accelerates count forecasts while retaining the database cache for cold reads. Keys include station, version and issuance time; returned payloads are copied and live inventory/risk is recomputed, never memoized with counts. Shutdown awaits worker cancellation before disposing connections.

## API compatibility

Public routes live under `/api/v1`. Additive response changes are preferred. Breaking changes require a new URL version and a documented migration window. OpenAPI is enabled unless `DOCS_ENABLED=false`; cookie security and production HTTPS requirements are independent of docs visibility. See [API contracts](API.md).

## Testing strategy

- Backend: unit tests for domain/services, API contract tests through ASGI, repository/integration tests against PostGIS, and migration upgrade tests.
- Frontend: Vitest and Testing Library for components/client behavior; deterministic offline Playwright checks and opt-in real PostgreSQL-backed browser flows. The latter exercise map selection, forecasts, history, recommendations and cookie accounts.
- ML: feature invariants, point-in-time leakage tests, deterministic small fixtures, baseline comparisons, calibration checks, and artifact compatibility tests.
- CI: lint/types/coverage, disposable PostGIS migration upgrade/downgrade/re-upgrade, offline UI checks, and a production-image nginx/API/PostGIS job exercising persisted browser workflows and bounded load. An opt-in deployment workflow is gated by CI, credentials and a protected production environment. Docker and GitHub workflow execution remain unverified on the current Docker-less Windows host; native PostgreSQL/API/browser checks were actually executed. See [testing architecture, commands and measured results](TESTING.md).

## Implementation phases

1. **Foundation (delivered):** repository/tooling, responsive shell and map, versioned API, PostGIS models/migration, health/observability, Docker and CI.
2. **Station data (delivered):** real GBFS discovery, normalized station aliases/status history, spatial inventory API, historical backfills, coverage-aware demand exports and offline provider fixtures. The rider UI now includes viewport-bounded station browsing, map clustering, freshness aging and observation timestamps.
3. **Trip planning (backend and rider flows delivered):** paginated/spatial catalog, fresh inventory, qualified count forecasts and explainable alternatives. Cookie-session accounts, isolated preferences and saved commutes are wired. Commute cards invoke the existing Python ranker for immediate checks; scheduled alert jobs record deduplicated owned notifications with fresh measurement/model evidence.
4. **Historical replay (observed demand delivered):** checksum-versioned coverage-aware publication, bounded indexed daily queries, DST-aware play/pause/speed controls, searchable clustered map and station charts. Stored final-period forecasts use the sealed archive-only inference path and are labeled retrospective, not recorded live. Map geography is current catalog geography. Actual historical inventory/outcome validation remains future measured-snapshot work.
5. **Forecasting (count system and presentation delivered):** evaluated baselines and LightGBM candidates, shared features, separate training image, immutable artifacts, count intervals, explanations, serving API and conservative fallbacks. React displays server forecasts/scenario limits and observed ride charts without duplicating ML logic. Multi-season training, timely trip counts, stockout calibration and persistent outcome monitoring remain.
6. **Hardening (automated QA delivered):** real PostGIS fault injection, chronological feature/model invariants, authenticated full-stack browser journeys, deterministic failure fixtures, coverage/report artifacts, bounded load measurements and opt-in release workflow. Local production-settings HTTPS is verified; hosted HTTPS/image builds, broader accessibility/security audits, backup/restore drills, retention/partition tuning and SLO dashboards remain follow-up work.

## Tradeoffs

- A modular monolith trades independent scaling for low operational burden and transactional simplicity. Ingestion or training can become separate processes without becoming separate services.
- Live polling runs in a lightweight opt-in in-process scheduler (enabled by Compose), with equivalent explicit operator commands and PostgreSQL advisory-lock/due-time coordination. A queue is deferred until measured scheduling or throughput needs demand one.
- The frontend now defaults to configurable OpenFreeMap vector styles with automatic source attribution, replacing public OSM raster tiles. The public provider has no SLA; deployments can supply an approved/self-hosted style at build time. Basemap/font requests are external; offline/privacy-sensitive deployments should host their own assets. See [frontend operations](FRONTEND.md).
- Development, production, and CI all use the committed npm lockfile; dependency updates are explicit and reproducible.
- Managed deployment defaults to one worker; existing Compose allows two. More workers duplicate models/caches. Large models require profiling before a new service boundary.
- Migration 0005 adds rider ownership and replay tables without changing previous station/trip/ML schemas. Saved settings are validated bounded JSONB, not a second trip-planning engine. Alerts use parent/rule locks, occurrence uniqueness, cooldown and the existing advisory-lock scheduler. Replay duplicates the sealed derived export for predictable daily reads, with provenance and transactional activation; retained inactive versions need operator disk review. See [rider runbook and verification](RIDER_FEATURES.md).
- Migration 0006 explicitly marks synthetic replay imports. `compose.demo.yml` isolates two synthetic stations and deterministic replay in a separate database; provider jobs are disabled. Synthetic counts never become measured availability risk or alert evidence. The same approved count artifact can supply an explicitly unvalidated pooled reference, not a secretly trained demo model.
- Artifacts persist as reviewed immutable image files; raw backfill/checkpoint work remains separate. A small managed DB/web plan is a starting budget, not a hosted capacity guarantee. Docker builds and provider deployment remain unexecuted locally; [final verification](FINAL_VALIDATION.md) separates observed results from remaining acceptance gates.

