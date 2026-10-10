# Executed backend validation

Executed October 9, 2026, Pacific time. Generated UTC timestamps in `data/validation/service.json` fall on October 10. This record supersedes the earlier phase's local PostgreSQL/Python 3.12 verification limitation without claiming that Docker images were run.

## Actual environment

Docker Desktop is absent. The existing Ubuntu WSL environment was used to provision a local PostgreSQL 16.9/PostGIS 3.5.3 database named `bike_service_validation`. It is an isolated development verification database, not a production environment. A generated random database credential was supplied only to validation processes; no production secret was checked into source.

The API and final automated checks ran natively on Windows with bundled Python **3.12.14**, in an isolated virtual environment installed from `backend/pyproject.toml`. Resolved runtime versions included FastAPI 0.143.0, SQLAlchemy 2.1.4, asyncpg 0.32.0 and pinned LightGBM 4.6.0. Earlier checks also passed on the existing Python 3.10 compatibility runner. Final frontend checks used bundled Node **24.19.0**; the initial system Node 16 attempt failed the project's required modern runtime and was rerun successfully with the bundled runtime.

All four Alembic migrations were applied to real PostgreSQL. `alembic check` reports no metadata drift; extension-owned `spatial_ref_sys` is deliberately excluded from application autogeneration. A fourth additive migration indexes snapshot retention/fetched timestamps, snapshot-to-feed lookups and operational job timestamps.

## Real persisted data

The two completed January–February 2025 checkpoints were published, without downloading another trip archive. PostgreSQL contains:

| Persisted resource | Verified count |
| --- | ---: |
| Real selected stations | 100 |
| Unique normalized historical trips | 950,604 |
| Sparse per-run demand bins | 330,816 |
| Completed real import runs | 2 |
| Historical departures in selected scope | 611,768 |
| Historical arrivals in selected scope | 612,709 |

These reconcile with [the original validated import](DATA_VALIDATION.md). Repeat publication returned promptly without changing the totals. Actual GBFS discovery, catalog/status fetches and PostgreSQL live writes succeeded with 100 selected observations and zero rejections/unmapped records. Multiple subsequent TTL-respecting polls were exercised; snapshot/cache row counts naturally change over time and are recorded in the ignored generated verification report.

The initial publication attempt exposed a production-style timeout: the large PostgreSQL GROUP BY exceeded the new eight-second serving statement limit. The complete transaction rolled back. Publication was corrected to stream the independently verified SQLite disk-backed aggregates into PostgreSQL in batches, retaining transactional completion and bounded RAM. Both full archives then committed. Serving timeouts were not relaxed to accommodate backfill work.

The database measured approximately **672 MB** locally after publication and validation. A Python 3.12 API process measured approximately **150 MiB peak working set** after real endpoint/model/authentication calls. These are local observations, not a composed-stack load benchmark or a production memory guarantee.

## HTTP endpoint checks

`scripts/verify_service.py` used real network HTTP requests to a running Uvicorn server, not just an ASGI mock. It verified liveness/readiness, metadata/OpenAPI, bbox pagination, nearby PostGIS distances, station metadata/availability, persisted historical ride counts/provenance, four forecasts, pickup/destination recommendations, registration/login/logout, HttpOnly cookies, private preferences and denied/authorized operational access. Its temporary synthetic test account was removed afterward; station observations and trip inputs were real. The generated report contains no password, cookie or CSRF credential.

The approved artifact remains `v1-888b8997541c0ad2`. The old winter model returned low-confidence forecasts and suppressed current inventory risk, as intended. Real recommendations used fresh measured inventory where available and explicitly distance-only alternatives when observations were stale. No measured current bike count or historical inventory was synthesized from trip counts.

`scripts/verify_dependency_failure.py` started a second actual HTTP server with an intentionally unreachable PostgreSQL endpoint. It confirmed liveness 200, readiness/stations/login 503, safe error bodies and request IDs, then terminated that server. Unit tests separately cover unavailable ML fallback and unexpected exceptions without leaking internal details.

## Reproduce the checks

Use an isolated migrated test database, never a production database: integration tests create/remove synthetic accounts/stations and exercise retention. From `backend/`, with `python` pointing to a Python 3.12 virtual environment:

```powershell
python -m pip install -e ".[dev,ml]"
$env:DATABASE_URL = "<isolated PostgreSQL+asyncpg DSN>"
$env:APP_ENV = "test"
python -m alembic upgrade head
python -m ruff check .
python -m mypy app
python -m pytest -ra
python -m alembic check
python -m pip check

python -m app.modules.ingestion.cli --data-dir ../data/real publish-staged
python -m app.modules.monitoring.cli live
python -m app.modules.monitoring.cli forecasts
python -m app.modules.monitoring.cli retention

# In another terminal using the same DB, start the actual HTTP server.
# APP_PUBLIC_ORIGIN was http://localhost:5173 for this verification.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
python scripts/verify_service.py
python scripts/verify_dependency_failure.py
python -m pip wheel --no-deps . --wheel-dir ../data/validation/wheels
```

The live/forecast job commands succeeded; a subsequent retention command returned `not_due` because the background scheduler had already run it. Real database tests independently exercised actual retention deletion, preserving unexpired sessions. Native API shutdown was observed cleanly between verification runs.

Final backend suite: **67 passed, no skips**, including PostGIS publication/spatial consistency, auth rate-window expiry, session expiry/revocation/rotation, CSRF rotation/origin rejection, cross-user preferences, restricted operator access, catalog pagination/bbox/freshness, persisted forecast-cache consistency, unavailable models, distributed job locks/durable backoff, retention and ML invariants. Ruff and strict mypy were run, with native dependency failures corrected before the final pass. Frontend lint/build succeeded and **4 tests passed**; this does not claim a new authentication/planning UI or end-to-end browser journey.

The backend wheel was also built, installed into an isolated target directory, and imported from that installed location. Application/OpenAPI creation and loading the packaged approved inference artifact succeeded, checking that distribution includes the model and its required source files.

## Remaining deployment verification

Docker image builds, nginx/TLS ingress behavior, secure-cookie handling over actual HTTPS, multi-worker load tests, backup/restore drills and remote GitHub Actions execution are not claimed. Compose YAML is checked structurally, not substituted for a Docker run. The WSL package versions are suitable for this isolated execution record only; deploy maintained images/hosts and current supported patches.

The subsequent frontend phase delivered account/planning flows and actual browser verification; see [frontend operations and execution record](FRONTEND.md). Saved journeys, verified-email recovery, sustained fresh inventory outcomes and current multi-season ML training remain. Scenario risk is not empirically calibrated stockout probability. See [API operations](API.md), [security configuration](SECURITY.md) and [ML limitations](ML.md).
