"""Sessions, private preferences, durable throttles and operations.

Revision ID: 0003_service
Revises: 0002_data_infrastructure
"""

from alembic import op

revision = "0003_service"
down_revision = "20261010_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = """
    CREATE TABLE users (
      id uuid PRIMARY KEY, email varchar(254) NOT NULL UNIQUE,
      password_hash text NOT NULL, is_admin boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE auth_sessions (
      token_hash char(64) PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      csrf_hash char(64) NOT NULL, expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_sessions_expiry ON auth_sessions(expires_at);
    CREATE INDEX ix_sessions_user ON auth_sessions(user_id);
    CREATE TABLE user_preferences (
      user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      max_walk_meters integer NOT NULL DEFAULT 1000 CHECK(max_walk_meters BETWEEN 100 AND 3000),
      reliability_weight double precision NOT NULL DEFAULT 0.7
        CHECK(reliability_weight BETWEEN 0 AND 1),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE request_limits (
      key char(64) PRIMARY KEY, window_start timestamptz NOT NULL,
      attempts integer NOT NULL CHECK(attempts>0)
    );
    CREATE INDEX ix_request_limits_expiry ON request_limits(window_start);
    CREATE TABLE operation_jobs (
      id uuid PRIMARY KEY, kind text NOT NULL,
      status text NOT NULL CHECK(status IN ('running','succeeded','failed','abandoned')),
      started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
      details jsonb NOT NULL DEFAULT '{}'
    );
    CREATE INDEX ix_jobs_kind_time ON operation_jobs(kind,started_at DESC);
    CREATE TABLE forecast_cache (
      station_id text NOT NULL, model_version text NOT NULL,
      as_of timestamptz NOT NULL, expires_at timestamptz NOT NULL,
      payload jsonb NOT NULL, PRIMARY KEY(station_id,model_version,as_of)
    );
    CREATE INDEX ix_forecast_cache_expiry ON forecast_cache(expires_at);
    """
    for statement in schema.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in (
        "forecast_cache",
        "operation_jobs",
        "request_limits",
        "user_preferences",
        "auth_sessions",
        "users",
    ):
        op.execute(f"DROP TABLE {table}")
