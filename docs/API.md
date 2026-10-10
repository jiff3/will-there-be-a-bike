# Backend API and operations

The service uses actual PostgreSQL/PostGIS stations, trip provenance, measured GBFS observations and the deployment-approved inference artifact. No network calls or model training run in HTTP handlers. Browser authentication uses database-backed cookies; recommendations are public, stateless planning requests.

Interactive OpenAPI documentation is at `/docs`, with schemas at `/openapi.json`. Set `DOCS_ENABLED=false` to disable both. All application routes use `/api/v1`. Errors contain `error.code`, `message`, `request_id` and optional safe validation details. Inputs, passwords, cookies and SQL parameters are not echoed. Responses include `X-Request-ID` and `Server-Timing`. Oversized JSON requests receive 413; invalid fields/ranges receive 422; unavailable dependencies receive 503.

## Start and populate

Development Compose applies migrations automatically and enables coordinated background jobs. On an empty database, the live job selects 100 official stations nearest Midtown. Existing persisted real-station scope is preserved on subsequent catalog updates. Public availability endpoints exclude demo stations by default.

```powershell
Copy-Item .env.example .env
docker compose up --build

# Publish an already completed local historical workspace without another download.
docker compose exec backend python -m app.modules.ingestion.cli --data-dir /app/data/real publish-staged

# Explicit operations; intervals and duplicate-job locks still apply.
docker compose exec backend python -m app.modules.monitoring.cli live
docker compose exec backend python -m app.modules.monitoring.cli forecasts
docker compose exec backend python -m app.modules.monitoring.cli retention
```

Historical workspaces must first be created with [the ingestion commands](DATA_INGESTION.md). Publication is atomic per archive, batches trips and disk-backed aggregates, checks conflicting identities and returns immediately for an already committed matching run. A timeout/failure cannot leave a partly published run marked complete. Heavy backfills and [training](ML.md) remain explicit offline jobs, not web requests.

Use the coordinated operations `live` command when the background scheduler is enabled; do not also run a separate legacy `collect --watch` poller. For entirely offline demo work, set `BACKGROUND_JOBS_ENABLED=false` and explicitly include labeled demo stations in station queries. Native API `/docs` has a different origin from Vite; account calls must originate from `APP_PUBLIC_ORIGIN` (or use a non-browser cookie client with that Origin), not bypass the Origin checks for Swagger.

## Public routes

| Route | Contract |
| --- | --- |
| `GET /stations` | `limit` 1–100, `offset` 0–10,000, literal name `search`, `bbox=west,south,east,north`, `available=bikes` or `docks`, explicit `include_demo`. Stable external-ID order, `items` and `next_offset`; no unbounded count query. |
| `GET /stations/nearby` | `lat`, `lon`, `radius` in straight-line meters (50–5,000), `limit` 1–100. Returns inventory and approximate `walking_meters`. |
| `GET /stations/{id}` | Current catalog name, coordinates, capacity, catalog update time and latest observation. IDs are strings; inactive/unknown stations return 404. |
| `GET /stations/{id}/availability` | Latest measured bikes/docks, operation flags, all observation timestamps, and `freshness` (`fresh`, `stale`, `missing`). Inventory is not replaced by zero when missing. |
| `GET /stations/{id}/demand` | Required timezone-aware `start`/`end`, 15-minute boundaries, at most seven days. Sparse observed counts from globally deduplicated persisted trips, with import provenance. Omitted bins are **not assumed zero**. Use coverage-aware exports for ML. |
| `GET /stations/{id}/predictions` | Four 30/60-minute arrival/departure forecasts, intervals, confidence, drivers, warnings and model version. Count forecasts are cached by station/version/issuance; inventory risk is recomputed from persisted observations. |
| `POST /forecasts/predict` | Existing explicit forecast interface: `station_id`, optional aware aligned `as_of`. Current/recent issuance only; CLI handles replay. No client-invented inventory fields. |
| `GET /forecasts/model` | Approved artifact identity, source/configuration and measured evaluation metadata. Missing/corrupt/incompatible models produce 503. |
| `POST /recommendations/trip` | Origin/destination coordinates, aware departure time, optional walking/reliability preferences and result limit. Returns separately ranked pickup/destination alternatives with reasons and degradation modes. |
| `GET /metadata` | Service/API version, provider, supported forecast horizons and approximation disclosures. |
| `GET /health/live` | Process-only liveness, even if the DB/model is unavailable. |
| `GET /health/ready` | Database connectivity and exact required Alembic revision. Missing/old schema or unreachable DB returns 503. Model failure does not disable non-ML station services. |

Example recommendation request:

```json
{
  "origin": {"latitude": 40.748, "longitude": -73.985},
  "destination": {"latitude": 40.755, "longitude": -73.99},
  "departure_time": "<current timezone-aware ISO timestamp>",
  "preferences": {"max_walk_meters": 1000, "reliability_weight": 0.7},
  "limit": 5
}
```

Departure means intended bike departure, now through one hour ahead. The caller must allow time to walk to pickup. The cycling-time heuristic is great-circle distance × 1.2 at 15 km/h; it is not a route or traffic-aware travel time. Walking distance is great-circle distance × 1.3, not verified pedestrian routing. Barriers, bridges, accessible paths and closures can invalidate either estimate.

## Recommendation semantics

Up to 30 candidate stations are searched per endpoint using PostGIS. The walk limit is applied to the approximation, not just the radius. Known-usable stations rank before unknown-inventory stations, which rank before known-empty/closed stations. Within each tier, the score combines normalized walk distance and capped usable inventory. `reliability_weight` controls that tradeoff; it is not a probability.

For a fresh, operational station and an in-context model, cumulative 30/60-minute arrivals/departures are interpolated in two segments. The elapsed portion since forecast issuance is subtracted; no estimate extends beyond 60 minutes from issuance. Current reported inventory is an approximation to present inventory, not a perfectly time-synchronized initial condition. Estimated bikes are clipped to measured usable slots (`bikes+docks`); docks are the complement. The inventory contribution blends current inventory (40%) and scenario inventory (60%). Incoherent horizons suppress the scenario.

The returned modes are `uncalibrated_inventory_scenario`, `live_inventory_only` and `distance_only`. Missing/unavailable/old/out-of-season ML falls back to fresh measured inventory. Stale/missing/synthetic inventory never supplies an estimated future stock level. Distance-only results explicitly identify their limitation and are not claims that the station will work. Database failure returns 503 because even the candidate catalog is unavailable.

The bundled winter model is old for October 2026, so real present-day tests correctly use low-confidence/live-inventory fallback. No recommendations claim calibrated stockout probabilities. The separate forecast endpoint's bounded Gamma-Poisson risk simulation has additional [documented assumptions](ML.md), not independently validated inventory outcomes.

## Accounts and preferences

`POST /auth/register` and `/auth/login` accept email plus a 12–128-character password. They require the exact configured `Origin` and return public user identity plus a CSRF token. The browser receives an opaque HttpOnly session cookie; retain the CSRF token only in memory. Registration returns 201, invalid credentials 401, duplicate/unavailable registration 409, and throttled attempts 429 with `Retry-After`.

Use `GET /auth/me`, `GET /auth/preferences`, `PUT /auth/preferences` and `POST /auth/logout`. Preference writes/logout require `Origin` plus `X-CSRF-Token`. Preferences are selected only by the authenticated session's user ID; there is no writable `user_id` parameter. The public planner takes explicit preferences: the frontend can fetch a signed-in user's preferences and pass them into a planning request without persisting the journey.

After a page reload, `POST /auth/csrf` rotates the token using the existing session and trusted Origin, without requiring a lost previous CSRF token. The previous token becomes invalid, including in other tabs. Reauthentication rotates the session; logout immediately revokes it in PostgreSQL.

```javascript
const login = await fetch('/api/v1/auth/login', {
  method: 'POST', credentials: 'include',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, password }),
});
const { csrf_token } = await login.json(); // memory only, never localStorage
await fetch('/api/v1/auth/preferences', {
  method: 'PUT', credentials: 'include',
  headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf_token },
  body: JSON.stringify({ max_walk_meters: 700, reliability_weight: 0.8 }),
});
```

Browsers supply Origin on these POST/PUT requests. Non-browser clients must send it explicitly and maintain a cookie jar. Same-origin deployment is preferred; allowed development CORS origins are explicit.

## Restricted operations and retention

`GET /operations/status` requires an authenticated administrator (401 without a session, 403 for regular users). A trusted local operator can grant the role:

```powershell
docker compose exec backend python -m app.modules.monitoring.cli grant-admin --user-id USER_UUID
```

The status response contains recent ingestion/feed/job records, worker-local request counts/timing sums, forecast-cache hit/miss/low-confidence counters and pool status. Metrics are per worker since startup, not falsely presented as cross-worker aggregates. There is no public arbitrary job/model-upload/admin-grant API.

Each process can run the lightweight scheduler. Dedicated checked-out PostgreSQL session advisory locks and persisted due times prevent concurrent live/forecast/retention jobs across workers. A disconnected process releases its lock; the next owner marks orphaned running records abandoned. Live polling respects configured intervals, publisher TTL, rate-limit Retry-After and bounded failure backoff. Forecast refresh runs every 15 minutes for up to 120 active real stations; it records the actual version and never trains. Shutdown signals the scheduler, allows a 20-second grace before cancelling pending work, and disposes the pool. Blocking provider threads may finish later within their bounded network/retry timeouts.

Hourly retention removes at most 10,000 rows per table per pass: snapshots older than `SNAPSHOT_RETENTION_DAYS` (default 30), unreferenced old feed envelopes, expired sessions/forecast cache, rate-limit records older than one day, and finished jobs older than `JOB_RETENTION_DAYS` (default 90). Raw historical trips/provenance and immutable model artifacts are not automatically deleted. Monitor retention lag before expanding station scope. Snapshot retention is a deliberate storage/validation tradeoff; preserve/export required outcome evidence before shortening it.

Connections use pre-ping, four pooled connections plus two overflow per production worker, three-second acquisition/connect limits, eight-second statements, two-second lock waits and ten-second command timeouts. Test processes use NullPool to isolate asyncio event loops. Writes use explicit transactions. Failed transactions are not blindly replayed; archive publication and live writes have idempotency protections for deliberate retries. Provider GET retries are bounded. Readiness reports unavailable dependencies instead of claiming success.

See [security/deployment configuration](SECURITY.md) and [the actual execution record](BACKEND_VALIDATION.md).

## Rider features

Migration `0005_rider_features` adds owned commute/alert CRUD, private notifications/read state and public coverage-aware historical replay. Their exact request/response conventions, scheduled-job semantics, bounds and reproducible publication/backtest commands are documented in [RIDER_FEATURES.md](RIDER_FEATURES.md). Private routes retain session/CSRF/Origin authorization and no-store responses. The alerts job joins live, forecasts and retention under the existing PostgreSQL advisory-lock scheduler; notification retention is 90 days.
