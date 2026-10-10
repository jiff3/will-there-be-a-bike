# Rider application

The React application consumes the existing `/api/v1` service. Demand models, intervals, risk simulation and recommendation ranking stay in Python. Browser calculations are limited to display units, timestamps, chart aggregation and freshness aging. There is no browser forecast model, simulated production dataset or invented route geometry.

## Start

Use Node 22.12+ (or a compatible version in `frontend/package.json`). The Windows system Node 16 runtime is not supported. The frontend needs a migrated, populated API; follow [backend operations](API.md) to populate real stations and historical trips.

```powershell
# From frontend/
npm ci
npm run dev
```

Open `http://localhost:5173`, not `127.0.0.1:5173`, when `APP_PUBLIC_ORIGIN=http://localhost:5173`. Hostnames are distinct origins for authentication. Vite proxies `/api` to `http://localhost:8000`; `VITE_DEV_PROXY_TARGET` can select another API. Native frontend environment variables can go in ignored `frontend/.env.local`; Compose reads the root `.env`.

Production uses the existing nginx same-origin proxy. Secure cookies require an actual HTTPS ingress and the matching backend public origin. Never put private credentials in `VITE_*` variables. See [security](SECURITY.md).

## Experience and contracts

- Station browsing requests up to 100 stations per page, capped at 1,000 per viewport with a truncation disclosure. Map movement is debounced. Search in the station list filters that loaded view; planner autocomplete separately searches the persisted catalog, including destinations outside the viewport.
- MapLibre renders clustered GeoJSON, not hundreds of HTML markers. Individual marker numbers are measured bikes or docks; cluster numbers count stations. Gray means stale, unknown, closed or synthetic, not zero. A keyboard-accessible station list mirrors selection. The selected station is deep-linkable with `?station=ID`.
- Station detail separates measured inventory from demand outlook and recorded rides. Inventory shows operation flags, report/feed/collection timestamps, usable slots and catalog capacity. Capacity differences are not silently filled with imaginary bikes.
- Demand plots use server means and intervals at the 30/60-minute **issuance** horizons. The exact window is displayed because it does not roll forward on every page render. Low-context predictions and invalid interval coverage remain explicitly qualified. Risk is shown only if supplied by the API, labeled as an uncalibrated scenario; unavailable risk is not replaced by a confident badge.
- Historical charts default to the last day in the model's actual reference archive, with an editable New York calendar date. Both date boundaries respect daylight saving. Hour bars sum returned quarter-hour observed rides; missing bins are not filled with zero. Exact timestamped observations remain available in a table. These are not stockout or inventory histories.
- Planning accepts station landmarks, explicit WGS84 coordinates or map points. Intended bike departure must be now through one hour ahead; custom times use the device timezone and are sent as aware ISO timestamps. Allow walking time before departure. Approximate walking distances/cycling times, reasons, tier order and future inventory come from the backend. No map route line is drawn because no pedestrian/cycling router is configured.
- Planner changes cancel obsolete requests and invalidate old alternatives. Recommended station markers remain in the map data even outside the catalog viewport. Results older than a minute prompt refresh. Ranking scores/weights are never presented as availability probabilities.
- Account screens implement registration, login, session restoration, private walking/ranking defaults, CSRF recovery/rotation and logout. The cookie is HttpOnly; CSRF stays in React memory. No credential or journey is stored in localStorage. Reload recovers CSRF only when a protected write needs it. A rejected rotated-CSRF write is retried once with a new token; failed/ambiguous login or other writes are not blindly replayed.
- Saved trips, notification subscriptions, password recovery and email verification are **not implemented**. The account page states this plainly and does not offer fake save/notify controls. Ordinary users do not receive restricted operational data.

## Basemap, attribution and network use

Default style: `https://tiles.openfreemap.org/styles/positron`. [OpenFreeMap's official guide](https://openfreemap.org/quick_start/) documents this style/MapLibre integration; its [license and attribution documentation](https://openfreemap.org/#attribution) describes the MIT project and separate OpenStreetMap/OpenMapTiles obligations. MapLibre displays the style source's OpenFreeMap, OpenMapTiles and OpenStreetMap attribution without hiding it. The public instance has no SLA; use a suitable provider or self-hosted tiles for deployment requirements.

Set `VITE_MAP_STYLE_URL` to another public MapLibre style URL. Development Compose passes it to Vite; production Compose passes it as a frontend **build argument**, so rebuild the web image after changing it. Preserve the new provider's required attribution. The old public OSM raster endpoint is no longer the default.

Basemap providers receive tile requests revealing the viewed area. Google Fonts supplies optional DM Sans/Newsreader typography; system/Georgia fallbacks work when blocked. Configure/self-host style assets and fonts for stricter privacy/offline deployments. No third-party geocoding, routing, location permission or tracking service is requested by the application.

## Loading, failures and offline behavior

Catalog and selected station observations poll serially every 60 seconds; forecasts every five minutes, with the backend owning quarter-hour count caching. Polls pause in hidden tabs and while offline, resume on visibility/connectivity, and abort on selection/unmount. HTTP requests have a 15-second timeout and preserve safe API error codes/request IDs/validation messages. Transient polls retain last successful data, which continues aging into stale state. Explicit retry controls are provided; a missing model never removes station observations or planning inputs.

If WebGL, tiles or network fail, the map reports the limitation and offers retry; station browsing and coordinate/landmark planning remain usable when the API is reachable. Disconnected browsers do not generate replacement station values. `?demo=1` explicitly includes backend-seeded synthetic stations and labels that mode; it does not generate fixtures in the browser or make demo stations eligible for real recommendations. Seed demo data with the ingestion CLI and disable provider jobs for fully offline development.

## Tests

```powershell
# From frontend/
npm run format:check
npm run lint
npm run typecheck
npm run test:run
npm run build
npm run test:e2e

# Opt-in real integration checks: populated local PostgreSQL-backed API required.
$env:BIKE_E2E_REAL = '1'
npx playwright test e2e/real-backend.spec.ts
Remove-Item Env:BIKE_E2E_REAL
```

Default Playwright checks intercept API requests with clearly synthetic test-only fixtures and block basemap/font requests; they require neither PostgreSQL nor internet at test runtime. Real tests do not intercept API/data/tiles. The real map test targets the validated Midtown dataset, clicks an actual MapLibre station, loads both forecast horizons and persisted historical rides, plans to another station and inspects desktop/mobile layouts. The account test creates a disposable `frontend-e2e-TIMESTAMP@example.com` account, checks HttpOnly cookies, reload/CSRF recovery, persisted preferences, logout revocation and login. Run only against a non-production database. It does not send email. A local operator should delete only those exact test accounts afterward; there is intentionally no public account-delete/admin API.

Unit/component tests cover the transport, cancellation and obsolete responses, polling failure retention, New York DST, missing/stale observations, forecast/scenario semantics, sparse history, planner contracts/global search, account errors, delayed preference loading and CSRF rotation. Coverage is diagnostic, not a claim of complete accessibility/security testing. Screenshot artifacts and browser reports are ignored under `frontend/test-results` and `frontend/playwright-report`.

## Executed verification — October 9, 2026 (Pacific)

The existing native FastAPI process and isolated WSL PostgreSQL 16/PostGIS database were used, with 100 real stations, 950,604 persisted January–February 2025 trips and approved artifact `v1-888b8997541c0ad2`. No new model was trained or demand ranking implemented in JavaScript. Real GBFS observations and OpenFreeMap tiles were accessible. This was not a Docker deployment test.

Commands actually completed with Node 24.19.0:

- `npm run format`, `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm run build` — passed.
- `npm run test:run` — **27 passed** across five files. Diagnostic line coverage was 78.75%; this is not full application/a11y coverage.
- `npm run test:e2e` — **two offline browser tests passed**; two real-data tests intentionally skipped without opt-in.
- `BIKE_E2E_REAL=1 npx playwright test e2e/real-backend.spec.ts` — **both real-data browser journeys passed**, without API interception. Desktop 1440×960 and mobile 390×844 views had no horizontal overflow.
- Backend `python -m pytest -ra`, with the isolated PostgreSQL DSN and test configuration — **67 passed, no skips**.

Browser inspection exposed and fixed catalog-only autocomplete hiding an off-viewport destination, persisted preferences loading after the account form, misleading freshness during polling, and map-worker readiness in the click test. Keyboard landmark navigation supports arrows, Enter and Escape. The winter-only model correctly returned low confidence in October; the UI showed that limitation, suppressed unsupported risk claims and presented the server's measured-inventory fallback recommendations.

Final teardown exposed generated coverage/browser reports triggering Vite reloads, so those output folders are excluded from its watcher. A cold-start smoke assertion was also corrected to wait for the map's existing bounded failure state rather than assuming the lazy renderer loads within five seconds. Both offline checks and the real map journey were rerun successfully after these changes; the final formatting/lint/type checks passed.

Real browser screenshots were preserved at ignored `data/validation/frontend-desktop.png` and `data/validation/frontend-mobile.png`. Four exact disposable accounts created during these runs were deleted afterward; real station/trip data was preserved. The owned Vite preview process was stopped after verification; restart with `npm run dev`. The pre-existing API was left running. The Sites skill was used only for local preview/verification guidance: no hosting project, publication or framework replacement was performed. Computer-use guidance informed the responsive browser inspection; no unrelated application settings were changed.

## Remaining product work

Saved commutes, scheduled in-app availability alerts, notification history and real demand replay are now implemented; see [rider feature contracts and reproduction](RIDER_FEATURES.md). Browser checks cover their persisted workflows at desktop/mobile sizes. Current multi-season forecasts and independently validated inventory outcomes are backend/data work, not frontend confidence styling. Verified routing/geocoding, account recovery, external notification delivery, offline basemap packaging, broader cities and formal assistive-technology/load audits remain future work. Docker image builds and actual HTTPS cookie behavior were not run in this Windows environment without Docker Desktop.
