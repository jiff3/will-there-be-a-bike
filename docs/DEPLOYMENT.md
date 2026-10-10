# Production deployment

## Status and choice

**Not deployed.** No hosting credentials/authorization were available. The Blueprint passed the current official `https://render.com/schema/render.yaml.json` on October 10, 2026; deploy response fields were checked against the official OpenAPI document. Local production-settings HTTPS browser tests passed. Docker is unavailable locally: image builds, provider deployment and hosted smoke tests remain required.

Primary option: one paid Render Docker web service, a private managed PostgreSQL 16/PostGIS database, provider HTTPS, and pre-deploy migration. Sleeping free compute cannot reliably collect observations/evaluate alerts. Current plans/fields and paid pre-deploy behavior: [Blueprint reference](https://render.com/docs/blueprint-spec), [deploy lifecycle](https://render.com/docs/deploys). [PostGIS extension support](https://render.com/docs/postgresql-extensions).

The starting budgets are 512 MB web, 256 MB DB, 5 GB DB disk, autoscaling off—not hosted performance qualifications. Check [current pricing](https://render.com/pricing) before authorizing costs; storage/bandwidth/workspace charges can be additional. Upgrade the DB if imports/query cache require it. Backfills/training run separately, not on the web service. Review disk before expanding beyond 100 stations/two months.

## Exact remaining owner steps

1. Push to your GitHub repository's `main`. Pass all four CI jobs on a Docker-capable runner before hosting. Locally run `docker compose -f compose.demo.yml up --build --wait --wait-timeout 180` and `docker build --target production -t bike-managed:local .`.
2. Connect the repo in Render and create a Blueprint from `render.yaml`; explicitly review/authorize paid resources. Supply `APP_PUBLIC_ORIGIN` as the **actual assigned** HTTPS origin, no path/slash. Do not assume the service name is available. Automatic deploys are off.
3. The first release bootstraps PostGIS/migrations with the managed owner URL. **Before public registrations**, provision a limited runtime login as below. Retain owner credentials only as secret `MIGRATION_DATABASE_URL`; replace `DATABASE_URL` with the runtime role's internal URL. Change the Blueprint's `DATABASE_URL` definition from `fromDatabase` to `sync: false` so future syncs cannot revert to owner credentials. This owner bootstrap is a documented compromise, not least privilege out of the box.
4. Confirm HTTPS origin, secure cookies, debug false, one worker, background jobs enabled, 60-second polling and private DB (`ipAllowList: []`). Optionally disable docs. No hardcoded session signing secret is needed: random session tokens are hashed in PostgreSQL.
5. Protect GitHub environment `production` with approval. Set environment secret `RENDER_API_KEY`; repository variables `RENDER_SERVICE_ID` (actual `srv-...` ID), `APP_PUBLIC_ORIGIN`, `RENDER_DEPLOY_ENABLED=true`. Run **Deploy to Render** manually on `main`. No secrets in YAML or public deploy-hook links.
6. The workflow reruns CI, deploys the exact SHA, waits at most 20 minutes and verifies frontend/readiness/model endpoints using verified TLS, including the approved model version in the reviewed commit. Ambiguous deploy writes are not blindly retried; failure requires reviewing private provider logs. No database downgrade. Contracts: [trigger deploy](https://api-docs.render.com/reference/create-deploy), [retrieve deploy](https://api-docs.render.com/reference/retrieve-deploy).
7. Manually confirm station freshness, cookie flags/login/logout, saved commute persistence, notification jobs, replay and model notices. Add a real README URL only afterward. Review backups/restore and abuse monitoring before broad public use.

## Database least privilege

In an authorized database-owner SQL session, create login `bike_runtime` with a strong new password from your secret manager. Never paste it into source/issues/shell history. Replace identifiers below with the actual database and migration owner:

```sql
GRANT CONNECT ON DATABASE DATABASE_NAME TO bike_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO bike_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO bike_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bike_runtime;
REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM bike_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE MIGRATION_OWNER IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO bike_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE MIGRATION_OWNER IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO bike_runtime;
```

No runtime SUPERUSER, CREATEROLE, CREATEDB, schema CREATE or ownership. If authorized setup needs external DB access, permit only a temporary exact client IP and remove it afterward. Owner/runtime DSNs stay in provider secrets. Raw `postgresql://` URLs adapt to asyncpg; `sslmode` maps to driver `ssl`. Use TLS externally.

`python -m app.migrate` chooses `MIGRATION_DATABASE_URL` before importing settings/engines. Workers do not migrate. Readiness enforces schema revision. Additive migration 0006 preserves real data and flags synthetic replay provenance. Rehearse backward-compatible rollback/restore, never automatic destructive downgrades.

## Artifacts, data and scheduled work

Approved inference artifacts, hashes and feature metadata are immutable image contents: no writable model disk required across restarts. Review/activate a retrained artifact in a new release; the server never trains or accepts uploads. Default provider filesystems are ephemeral: source ZIPs/checkpoints must not rely on them across deploys. [Disk tradeoffs](https://render.com/docs/disks).

The opt-in in-process scheduler bootstraps 100 real stations and polls discovered official feeds, respecting TTL/backoff. PostgreSQL advisory locks/persisted due times coordinate live, forecast, alert and retention jobs across overlapping workers. This is best-effort scheduling, not an exactly-once queue or guaranteed alert SLA. Alerts require fresh real observations; the old winter model suppresses unsupported risk.

Retention: snapshots 30 days, completed jobs 90 days, notifications 90 days; expired sessions/throttles/forecast caches and unreferenced feed records are deleted in bounded batches. Historical trips/replay are retained; review inactive versions/disk. Retention is not backup. Use trusted CLI commands for operator status/admin grants; public users cannot run arbitrary jobs.

Run ingestion/training locally per the runbooks, then transfer a reviewed PostgreSQL dump or publish using an authorized private/TLS connection. Do not backfill months inside a 512 MB web instance. Tests/QA never target the application database. Persistent model files ship in the image, not a raw-data download.

## HTTPS, proxy trust and recovery

Managed ingress terminates HTTPS; the app listens on `0.0.0.0:$PORT`. Exact-Origin/cookie policy does not rely on untrusted forwarded schemes. Production sends HSTS, frame denial, no-sniff, request IDs and private no-store headers. Logs contain templates/timings, not credentials/bodies.

`FORWARDED_ALLOW_IPS` defaults conservatively to loopback. Never blindly set `*` on public ingress: Cloudflare can append a forwarded chain. Without verified proxy handling, the IP throttle groups proxy peers (safe but potentially over-limiting); per-account limits still apply. Tune/test on the real host, not by trusting first X-Forwarded-For values. [Render ingress behavior](https://render.com/articles/host-pocketbase-on-render).

```bash
curl --fail https://YOUR_ACTUAL_HOST/api/v1/health/live
curl --fail https://YOUR_ACTUAL_HOST/api/v1/health/ready
curl --fail https://YOUR_ACTUAL_HOST/api/v1/forecasts/model
```

Readiness covers DB/schema, not feed freshness/model validity. Check model metadata/admin job status separately. Missing models leave stations/accounts usable; DB errors produce sanitized 503s. No ambiguous write retries. Monitor structured logs, request latency/error metrics and failed/abandoned jobs. Perform an actual backup restoration drill.

Existing nginx `compose.prod.yml` and immutable GHCR VPS workflow remain alternatives with owner-managed TLS, credentials, backups and SSH host-key pinning. See [security](SECURITY.md). No provider resources, image publication or remote release were performed here.
