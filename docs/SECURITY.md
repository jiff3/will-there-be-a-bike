# Security and production deployment

This phase implements local password accounts and revocable database sessions, not an identity-provider placeholder. It is a foundation for an internet-facing service, not a claim of a penetration test or completed production operations programme.

Saved commutes and alert rules contain private location/schedule preferences and inherit the same session-bound ownership, CSRF and exact-Origin checks. Foreign item IDs return 404, not their metadata. Private responses are no-store; React unmounts private workspaces when the account changes and guards inbox badges by account identity. Notifications remain private and expire after 90 days; deleting a commute stops its rules but retains earlier owned history. Replay publications are public derived ride counts, contain no rider trip identifiers, and are activated only by trusted local operator commands. See [rider operations](RIDER_FEATURES.md).

## Credentials, cookies and CSRF

Passwords use salted Argon2id with 19 MiB memory, three iterations and one lane. At most two hashes/verifications run concurrently per process; work runs outside the event loop. Unknown-user login performs a dummy verification. The implementation follows the parameter guidance in [OWASP password storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) and uses [argon2-cffi](https://argon2-cffi.readthedocs.io/en/stable/parameters.html). Passwords are not logged or returned. Validation responses remove raw inputs and exception context.

Session/CSRF tokens each contain 32 cryptographically random bytes. Only SHA-256 token digests are stored; no hardcoded signing secret is needed. Production cookies use `__Host-bike_session`, `Secure`, `HttpOnly`, `SameSite=Lax`, Path `/`, and no Domain. Development HTTP uses `bike_session`. Absolute expiry defaults to seven days; logout deletes the row, and login rotates the session. Sessions are not stored in localStorage. No browser JavaScript can read the HttpOnly credential.

Trusted-Origin checks protect login/registration and CSRF bootstrap. Authenticated preference writes/logout also require a session-bound synchronizer token. Token rotation after reload is an authenticated, same-origin POST, not a mutating GET. CORS is an explicit allowlist, not a replacement for CSRF checks. See [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html). Non-browser callers must deliberately supply Origin and the CSRF header.

Authentication attempts have shared PostgreSQL counters per client IP and normalized account, default ten attempts per 15 minutes. Atomic upserts coordinate workers; rejected attempts still count. Old counter rows expire through retention. IP digests are pseudonymous operational identifiers, not guaranteed anonymization. Registration's 409 can disclose that an email is unavailable; no email verification or password reset is implemented. Add verified-email recovery, stronger abuse detection and MFA before deployments that require those account-lifecycle controls.

Private preference queries derive the user ID solely from an active session. Regular users cannot access operational status or grant privileges. Admin granting is a trusted local CLI operation requiring database authority; audit who can run deployment commands. Revoke an admin by updating that owned account's `is_admin=false` through a trusted database maintenance transaction.

## Required deployment settings

Production startup fails if `APP_PUBLIC_ORIGIN` is not HTTPS, `AUTH_COOKIE_SECURE` is false, debug is enabled or the development database password remains in the URL. The origin must be exact (for example `https://bikes.example.org`), with no path/trailing slash. Use explicit CORS origins. Generate unique database credentials outside source control, URL-encode them in `DATABASE_URL`, and keep `.env` out of Git. Use a maintained host and current supported PostgreSQL/PostGIS patches; the old WSL validation environment is not a production deployment recommendation.

Production Compose publishes nginx only on port 8080. Terminate HTTPS at a trusted reverse proxy in front of it. Plain HTTP on port 8080 is not a usable production authentication entry point because secure cookies will not be sent. Bind/restrict that port to the TLS ingress with host firewall rules. PostgreSQL and the API must remain unexposed.

The API trusts forwarded client IPs only because production Compose's API is private and nginx **overwrites** incoming `X-Forwarded-For` with its observed peer. If another TLS proxy sits ahead of nginx, configure nginx `set_real_ip_from` for that proxy's exact address/CIDR and `real_ip_header` before expecting per-client rate limits. Otherwise requests are safely grouped under the proxy IP and may be over-limited. Never accept arbitrary public forwarded-header chains or publish the backend while using `FORWARDED_ALLOW_IPS=*`.

## Migration and database privileges

Development migrates automatically. Production workers do not migrate; run a single release job before starting them. Use a migration-owner credential for this command and a different, non-superuser runtime credential in the normal Compose environment:

```powershell
# .env contains the runtime DATABASE_URL and HTTPS APP_PUBLIC_ORIGIN.
# Obtain the migration DSN from the deployment secret manager, not this repository.
docker compose -f compose.prod.yml build
docker compose -f compose.prod.yml up -d db
docker compose -f compose.prod.yml run --rm -e DATABASE_URL=$env:BIKE_MIGRATION_DATABASE_URL backend alembic upgrade head
docker compose -f compose.prod.yml up -d
```

Provision PostGIS with an authorized database owner/operator before migrating if the migration role cannot create extensions. Grant the runtime role CONNECT on its database, USAGE on the application schema and SELECT/INSERT/UPDATE/DELETE on required application tables, plus sequence access if needed. It needs SELECT on `alembic_version`, not permission to change it. Set matching default privileges for future migration-owned tables. Do not grant runtime SUPERUSER, CREATEROLE, CREATEDB, schema creation, or ownership of the migration schema. Restrict PostgreSQL network access and use TLS for non-local database connections. Readiness checks the exact schema revision and prevents an unmigrated backend from being marked healthy.

Database transactions are not automatically retried after ambiguous writes. Re-run only the idempotent operational commands once the failure is understood. Back up PostgreSQL and test restoration; snapshot retention is not a backup policy. Preserve offline model/config/source checksums alongside deployment artifacts.

## HTTP and artifact boundaries

JSON bodies are limited to 64 KiB in nginx and the API. Request IDs accept only bounded safe characters; application logs use route templates, never raw queries, cookies, Authorization headers or request bodies. Structured dependency errors do not reveal SQL, connection URLs or credentials. Account responses use `Cache-Control: no-store`; browsers receive `nosniff` and a same-origin referrer policy. Disable docs with `DOCS_ENABLED=false` if required by deployment policy.

Public station, forecast and planner endpoints intentionally require no account. Candidate counts, spatial radii, date ranges, input size, query time and connection pools are bounded. Add ingress-wide per-client quotas and upstream DDoS protections for a public launch; the implemented durable auth throttle is not claimed to be general DDoS mitigation. Recommendation POSTs do not mutate private user data.

Model loading accepts only deployment-controlled local native text/JSON artifacts. File hashes, paths, sizes, library version and feature implementation guards prevent corruption/incompatibility, not hostile native-parser exploits. Never expose model upload/path selection to API clients. Load once per process and restart workers after a reviewed activation/rollback to a compatible version.

Remaining work includes email verification/recovery, administrator audit trails, sustained load/security testing, TLS ingress verification, backup/restore drills, dependency scanning and operational alerting. The current model's old/out-of-season fallback is intentional; it must not be relabeled a reliable current availability predictor.
