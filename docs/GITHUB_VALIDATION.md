# Container and GitHub validation

The public repository is [jiff3/will-there-be-a-bike](https://github.com/jiff3/will-there-be-a-bike). All four CI jobs passed on October 10, 2026 in [run 38088411123](https://github.com/jiff3/will-there-be-a-bike/actions/runs/38088411123), testing commit `73db64e1a397bd57879c63de342babc235f60a26`. The README badge reports the latest run rather than a permanently green image.

## Executed results

| Job | Evidence |
| --- | --- |
| Backend | Ruff, formatting, mypy; all six migrations upgraded, downgraded, re-upgraded and checked for drift; **173 pytest tests passed**, zero skips/failures, **81.67%** application coverage, **14.34 s**. Real containerized PostgreSQL 16/PostGIS, no live provider calls. |
| Frontend | Lockfile installation, lint, TypeScript/Vite production build, formatting; **44 Vitest tests**, **78.37%** line coverage; **two Chromium UI smoke tests**, **4.0 s**. |
| Production Python/nginx images | Actual images built from a clean checkout; disposable database migrated/seeded; all services healthy; **six persisted Chromium journeys**, **34.6 s**, followed by a bounded HTTP load smoke. |
| Managed single-origin image | Root production Dockerfile built from a clean checkout; isolated offline database migrated and seeded; frontend/API/model worked together; **two browser walkthroughs**, **8.3 s**, including account/commute persistence. |

The browser cases cover discovery, server forecasts, recommendations, authentication/CSRF, saved commute CRUD, actual alert evaluation/deduplication/read state, cross-user isolation, replay playback/boundaries, mobile layout and offline/stale/empty states. Synthetic QA data is labeled and isolated. Tests do not require Citi Bike, basemap services or production secrets. Coverage/JUnit/browser traces/videos/container diagnostics are retained as workflow artifacts.

The added migration regression also passed locally. A full native repeat passed **173 tests in 64.10 s**, **81.76%** application coverage; `alembic check` found no drift in the isolated native migration database. These are separate measurements from the runner results above.

## Bounded runner performance smoke

Actual nginx → FastAPI → PostGIS/model HTTP, four users, 15 requested seconds, one API worker, 100 deterministic QA stations:

| Query | Requests | Failures | Requests/s | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bounding-box stations | 121 | 0 | 8.20 | 37 | 56 | 69 |
| Nearby stations | 107 | 0 | 7.25 | 52 | 74 | 91 |
| Inference | 86 | 0 | 5.83 | 53 | 73 | 110 |
| Recommendations | 49 | 0 | 3.32 | 110 | 350 | 1,000 |
| Aggregate | **363** | **0** | **24.61** | **50** | **110** | **140** |

Locust histogram percentiles are approximate; the short sample includes cache warming and is not a hosted SLO or a directly comparable rerun of the earlier native benchmark. A post-load Docker snapshot recorded nginx **5.395 MiB**, API **126.8 MiB**, database **142.5 MiB**. These are snapshots, not peak measurements, and exclude the runner/browser/build overhead. No API PID memory profile was supplied to this container benchmark.

## Defects caught by the first published runs

- Base rollback tried to drop provider-installed PostGIS, which also supported topology/geocoder extensions. It now drops application tables only and preserves database capabilities, with a regression test.
- Provider images add extension schemas to `search_path`. Migration sessions now explicitly use `public`; drift checks no longer misidentify provider tables as application removals. Unknown tables in the application schema still participate in drift detection.
- The nginx health probe used `localhost`, whereas the configured listener is IPv4. The probe now uses `127.0.0.1`; the corrected stack and browser tests passed. Container health-check output is retained for future diagnostics.

The initial failed runs remain visible in Actions. The fixes were committed and rerun; no retries or weakened test gates converted those failures into passes.

## Still not a hosted deployment

Publication and CI do not provision Render, publish a live application, authorize hosting costs or exercise public-provider TLS. Deployment remains opt-in through [the runbook](DEPLOYMENT.md). Multi-season/stockout calibration, backup restoration, long soaks and broad browser/security/accessibility audits remain documented limitations. See [native data/model verification](FINAL_VALIDATION.md) for the real-data experiment, which is not replaced by synthetic CI measurements.
