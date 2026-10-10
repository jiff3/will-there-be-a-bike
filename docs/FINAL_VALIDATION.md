# Final verification record — October 10, 2026

## Acceptance status

This record preserves the native data/model/application audit before repository publication. Subsequent [GitHub validation](GITHUB_VALIDATION.md) passed all four jobs, production image builds and clean-checkout container startup. Docker remains unavailable on the native host, but those gates are now verified on a Docker-capable runner. No Render credentials/resources/live deployment exist; prepared configurations are not represented as a hosted deployment.

Require passing CI for each release commit, then apply the authorized hosting steps in [DEPLOYMENT.md](DEPLOYMENT.md). No production secrets, hosting purchases or production image publication were performed.

## Environment and separation

Windows, Python 3.12.14, Node 24, Playwright Chromium, native WSL PostgreSQL 16.9/PostGIS 3.5.3. Host: i7-6700, four cores/eight threads, **15.94 GiB RAM**. This is not physical 8 GB-host certification. Containers have documented hard budgets; training is separate. Native web RSS was sampled at 127.78 MiB during the real-data audit, not a combined DB/browser memory benchmark.

The original application database was preserved. QA, migration and offline-demo databases were isolated. Migration-only elevated privileges were revoked. A temporary limited audit credential was used instead of changing the original application login. Raw data/venvs/reports/certificates are ignored, not repository assets.

After verification, validation web processes were stopped, the temporary audit role removed, three test-owned demo accounts deleted, and the generated local TLS certificate/private key discarded. These credentials can be regenerated; original real data and application accounts were untouched. The anonymous real-data recording remains an ignored local artifact, with the reproducible script committed.

## Executed checks

| Area | Executed evidence |
| --- | --- |
| Backend | `pytest --require-postgis --cov=app --cov-report=xml --cov-report=term --junitxml=reports/final-junit.xml`: **172 passed**, zero skips/failures, **81.76%** application coverage, 45.03 s. |
| Static backend checks | Ruff check/format and strict mypy passed, **73 app files**. |
| Frontend | ESLint, Prettier, TypeScript/Vite production build; **44 Vitest tests passed**. Coverage: lines **78.37%**, statements **77.32%**, branches **71.28%**, functions **66.87%**. |
| Migrations | All six revisions: actual upgrade → full downgrade → re-upgrade → `alembic check` on a disposable PostGIS DB; no drift. Fresh offline DB also migrated/seeded. |
| Authenticated browser flows | Six full-stack Chromium journeys passed with production settings, local HTTPS, actual PostgreSQL and sealed inference. The 56.5-second rerun covered registration/login/logout, secure HttpOnly cookies, CSRF recovery, map/history/forecasts/recommendations, commute CRUD, actual notification triggering/dedup/read state, second-user isolation, playback boundaries, mobile/offline/stale recovery. No API mocks; a local basemap avoids external dependencies. |
| Offline UI smoke | Two isolated smoke tests passed; final repeat 16.8 s. |
| Real-data walkthrough | Actual public station → 60-minute outlook → recommendations → historical playback → mobile passed, 19.2 s total runner time. Four README PNGs inspected. A 2 MB WebM recording retained under ignored `data/final-verification/`. |
| Integrated offline seed | Three native seeds kept **312 synthetic trips**, **two synthetic stations**, **192 replay bins**; same run/checksum and `already_published` on repeats. Fresh schema, accounts/replay use PostgreSQL, not API mocks. |
| Offline persisted browser demo | Both actual-app walkthrough and account/commute journey passed together in **13.6 s**. Create/edit/reload/recommend/delete/logout work; synthetic alert stations are excluded and Save alert remains disabled. |
| Operational commands | Forced forecast/alert/retention jobs on isolated QA returned succeeded. Forecast refresh processed 100 stations; manual empty alert batch checked zero rules (not a triggering claim); actual triggers are covered by persisted browser tests. Retention ran with zero rows due; expired-row deletion/rollback are tested in PostGIS. |
| Production headers | Verified local TLS using the generated certificate as a trust root: readiness 200, request ID, no-sniff, frame denial and HSTS. Oversized auth request 413 retained HSTS/no-store. Unit tests also cover chunked oversize without buffering the excessive chunk. This is not a public certificate/provider TLS test. |
| Configuration | All workflow/Compose YAML parsed. Render Blueprint validated against the current official JSON Schema; quoted `"off"` fixes YAML 1.1 boolean coercion. Deploy response fields matched provider OpenAPI. Offline deployment tests verify exact SHA, no token forwarding and no ambiguous POST retry. |

The preceding [QA record](TESTING.md) preserves its distinct 145-test run, repeated browser checks and measured load benchmark. The final phase did not rerun that benchmark or relabel it as hosted performance.

## Real data and model verification

Previously downloaded/fully streamed January/February official archives are retained as completed disk-backed checkpoints, not raw ZIPs. This phase reran `validate` and idempotent `publish-staged`: **950,604 unique valid trips**, zero uncovered departure bins, **9,600 intentionally uncovered carry-in arrival bins**. Original SQL data contains 100 stations, 330,816 sparse demand rows, 566,400 replay bins and 192 retrospective comparison rows. Database size: approximately **778 MB**; local staging/export files: **696,098,278 bytes**. The demo DB is approximately 17 MB.

Official discovery/GBFS collection was actually rerun, including after local service restart: GBFS 2.3, **2,519 provider stations**, **100 selected**, no missing selected stations/rejected observations, **97 newly inserted / three duplicate** measurements on the recorded collections. The other 2,419 are out-of-scope IDs, not corrupt records. Child feed URL was discovered as `https://gbfs.lyft.com/gbfs/2.3/bkn/en/station_status.json`. Freshness later ages naturally; screenshots do not promise ongoing freshness with the audit collector disabled. No full raw-archive re-download was required in this phase.

Executed from `backend/`:

```bash
python -m app.modules.ingestion.cli --data-dir ../data/real validate --start 2025-01-01 --end 2025-03-01
python -m app.modules.ingestion.cli --data-dir ../data/real publish-staged
python -m app.modules.ingestion.cli --data-dir ../data/real collect --publish
python -m app.modules.ml.cli train --data ../data/real/features.jsonl --locations ../data/ml/locations.json --config ml-config.json --output ../data/final-verification/models --report ../data/final-verification/training-report
python -m app.modules.ml.cli evaluate --artifact app/modules/ml/artifacts/current.json --data ../data/real/features.jsonl --report ../data/final-verification/evaluation
python -m app.modules.ml.cli backtest --artifact app/modules/ml/artifacts/current.json --data ../data/real/features.jsonl --report ../data/final-verification/backtest
python -m app.modules.ml.cli predict --artifact app/modules/ml/artifacts/current.json --station 05c5e17c-7aa9-49b7-9da3-9db4858ec1fc --at 2025-02-25T08:00:00-05:00 --replay
```

The real-data retrain reproduced `v1-888b8997541c0ad2`, source SHA-256 `7d866d3678b0469585a4865619a4744fb7af07ade7e0e5ebd635b39d9f306447`. Wall time **48.5966 s**; sampled process peak **186,241,024 bytes / 177.6 MiB**; two CPU threads. Same chronological selection retained seasonal baselines for all four targets. Evaluate/backtest reproduced measured MAE/RMSE/Poisson-deviance results; no reported gain or calibration was invented. See [full report](ml-evaluation/REPORT.md).

The 2025 winter artifact is not a validated current-season risk model. App/metadata expose age/context fallback and uncalibrated risk status; synthetic/stale/unknown inventory does not produce a real risk estimate. Historical replay contains observed ride counts and explicitly later-computed comparisons, never fabricated bike inventory or allegedly live-recorded forecasts.

## Changes found through verification

- Single-origin SPA fallback initially mishandled Windows separators and served index for `.env`; paths are normalized and hidden/API/asset misses are now denied, with tests.
- Managed PostgreSQL URL schemes/SSL query names now normalize without exposing credentials; production defaults CORS to its exact HTTPS origin.
- Startup settings validation hides raw inputs in tracebacks, including database secrets; invalid driver/security configurations have explicit no-secret regression tests.
- An isolated usable demo was added using normal ingestion/publication, with explicit DB replay provenance, safe real-DB refusal, deterministic ZIP contents and no risky synthetic notifications.
- Oversized requests formerly bypassed normal logging/metrics/security headers; both fixed-length/chunked rejects now use the shared response completion path and reject excessive chunks before copying them.
- External Playwright servers no longer spawn Vite during self-signed HTTPS probing; production session assertions cover `__Host-` secure cookies.
- Rider selectors permit synthetic landmarks for demo commutes but exclude them from real-measurement alerts. Readiness/freshness/model validity and polling labels are distinguished.
- New walkthrough tests initially used guessed station/cancel labels; they were corrected to actual API station names/UI accessible labels. Those failures were test-authoring defects, not hidden passing runs.
- Release verification additionally checks that the live model version matches the reviewed commit's approved pointer and rejects a different deployed commit/model. Missing credentials are rejected before HTTP; no release was attempted.

## Remaining qualification

Actual managed deployment and hosted smoke/load tests remain open. Docker/production-image and GitHub runner execution subsequently passed; see [the separate record](GITHUB_VALIDATION.md). Also future work: multiple seasons, independently recorded stockout calibration, verified-email/recovery/MFA, broad browser/accessibility/penetration testing, long soaks, backup restoration and provider interruption drills. No claims of 100% coverage, exactly-once alerts, routing accuracy, public-host SLO or full 8 GB certification are made.
