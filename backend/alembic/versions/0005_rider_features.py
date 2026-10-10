"""Saved commutes, scheduled in-app alerts and immutable demand replay inputs.

Revision ID: 0005_rider_features
Revises: 0004_retention_indexes
"""

from alembic import op

revision = "0005_rider_features"
down_revision = "0004_retention_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = """
    CREATE TABLE saved_commutes (
      id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      config jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_commutes_owner ON saved_commutes(user_id,created_at);
    CREATE TABLE availability_alerts (
      id uuid PRIMARY KEY,
      commute_id uuid NOT NULL REFERENCES saved_commutes(id) ON DELETE CASCADE,
      station_id uuid NOT NULL REFERENCES stations(id),
      kind varchar(20) NOT NULL CHECK(kind IN ('pickup_empty','destination_full')),
      mode varchar(20) NOT NULL CHECK(mode IN ('inventory','estimated_risk')),
      threshold double precision NOT NULL CHECK(threshold >= 0 AND threshold <= 100),
      lead_minutes integer NOT NULL CHECK(lead_minutes IN (30,60)),
      cooldown_minutes integer NOT NULL CHECK(cooldown_minutes BETWEEN 15 AND 1440),
      enabled boolean NOT NULL DEFAULT true, last_checked_at timestamptz,
      last_status text NOT NULL DEFAULT 'not_checked'
    );
    CREATE INDEX ix_alerts_commute ON availability_alerts(commute_id);
    CREATE INDEX ix_alerts_due ON availability_alerts(enabled,last_checked_at);
    CREATE TABLE notifications (
      id uuid PRIMARY KEY,user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      rule_id uuid REFERENCES availability_alerts(id) ON DELETE SET NULL,
      scheduled_for timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      read_at timestamptz,title text NOT NULL,message text NOT NULL,evidence jsonb NOT NULL,
      CONSTRAINT uq_notification_occurrence UNIQUE(rule_id,scheduled_for)
    );
    CREATE INDEX ix_notifications_owner_time ON notifications(user_id,created_at DESC);
    CREATE INDEX ix_notifications_created ON notifications(created_at);
    CREATE INDEX ix_notifications_rule_time ON notifications(rule_id,created_at DESC);
    CREATE TABLE replay_imports (
      id uuid PRIMARY KEY,source_sha256 varchar(64) NOT NULL UNIQUE,source_label text NOT NULL,
      active boolean NOT NULL DEFAULT false,first_bucket timestamptz NOT NULL,
      last_bucket timestamptz NOT NULL,row_count integer NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX uq_replay_active ON replay_imports(active) WHERE active;
    CREATE TABLE replay_bins (
      import_id uuid REFERENCES replay_imports(id) ON DELETE CASCADE,
      station_id uuid REFERENCES stations(id),bucket_start timestamptz,
      departures integer CHECK(departures >= 0),arrivals integer CHECK(arrivals >= 0),
      PRIMARY KEY(import_id,station_id,bucket_start)
    );
    CREATE INDEX ix_replay_time ON replay_bins(import_id,bucket_start);
    CREATE TABLE replay_predictions (
      import_id uuid,station_id uuid,as_of timestamptz,model_version text,
      payload jsonb NOT NULL,computed_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(import_id,station_id,as_of,model_version),
      FOREIGN KEY(import_id,station_id,as_of)
        REFERENCES replay_bins(import_id,station_id,bucket_start) ON DELETE CASCADE
    );
    """
    for statement in schema.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in (
        "replay_predictions",
        "replay_bins",
        "replay_imports",
        "notifications",
        "availability_alerts",
        "saved_commutes",
    ):
        op.execute(f"DROP TABLE {table}")
