# Contributing

Use Python 3.12, Node 22+, Docker Compose v2 and PostgreSQL 16/PostGIS. Start with the isolated offline demo, then read [architecture](docs/ARCHITECTURE.md) and [testing](docs/TESTING.md). Deterministic automated tests must never contact providers or production databases.

Preserve the modular monolith, observed-versus-estimated labels, timezone/coverage semantics, ownership checks and shared Python features. Include failure tests and migrations for schema changes. Never commit credentials, archives, personal traces or unreviewed artifacts.

Before submitting: backend Ruff/format/mypy/real PostGIS pytest; frontend lint/build/Prettier/Vitest; persisted Playwright workflows; production image builds. Document executed/skipped checks truthfully. Do not tune models on the final test period or call synthetic fixtures measurements.
