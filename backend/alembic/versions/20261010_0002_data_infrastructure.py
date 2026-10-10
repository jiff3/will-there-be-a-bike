"""Data provenance, identity aliases, observations and demand.

Revision ID: 20261010_0002
Revises: 20261009_0001
"""

from alembic import op

revision = "20261010_0002"
down_revision = "20261009_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = """
    ALTER TABLE stations ADD COLUMN is_demo boolean NOT NULL DEFAULT false;
    CREATE TABLE station_aliases (
      system_id uuid NOT NULL REFERENCES bike_share_systems(id),
      alias text NOT NULL, station_id uuid NOT NULL REFERENCES stations(id),
      source text NOT NULL, PRIMARY KEY(system_id, alias));
    CREATE TABLE ingestion_runs (
      id text PRIMARY KEY, source text NOT NULL, checksum text NOT NULL,
      scope jsonb NOT NULL, is_demo boolean NOT NULL, state text NOT NULL,
      rows_seen bigint NOT NULL CHECK(rows_seen >= 0), invalid bigint NOT NULL CHECK(invalid >= 0),
      errors jsonb NOT NULL, published_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE historical_trips (
      ride_id text PRIMARY KEY, started_at timestamptz NOT NULL, ended_at timestamptz NOT NULL,
      start_station text NOT NULL, end_station text NOT NULL, is_demo boolean NOT NULL,
      CHECK(ended_at > started_at));
    CREATE INDEX ix_trips_departure ON historical_trips(start_station, started_at);
    CREATE INDEX ix_trips_arrival ON historical_trips(end_station, ended_at);
    CREATE TABLE trip_provenance (
      run_id text REFERENCES ingestion_runs(id), ride_id text REFERENCES historical_trips(ride_id),
      PRIMARY KEY(run_id,ride_id));
    CREATE TABLE historical_demand (
      run_id text REFERENCES ingestion_runs(id), station_alias text NOT NULL,
      bucket_start timestamptz NOT NULL, departures integer NOT NULL CHECK(departures >= 0),
      arrivals integer NOT NULL CHECK(arrivals >= 0),
      PRIMARY KEY(run_id,station_alias,bucket_start));
    CREATE INDEX ix_demand_station_time ON historical_demand(station_alias,bucket_start);
    CREATE TABLE feed_sync_runs (
      id uuid PRIMARY KEY, source text NOT NULL, fetched_at timestamptz NOT NULL,
      feed_updated_at timestamptz NOT NULL, version text NOT NULL, ttl integer NOT NULL,
      is_demo boolean NOT NULL, stations_seen integer NOT NULL, rejected integer NOT NULL);
    CREATE TABLE availability_snapshots (
      station_id uuid REFERENCES stations(id), reported_at timestamptz NOT NULL,
      feed_updated_at timestamptz NOT NULL, fetched_at timestamptz NOT NULL,
      sync_id uuid NOT NULL REFERENCES feed_sync_runs(id),
      bikes integer NOT NULL CHECK(bikes >= 0), docks integer NOT NULL CHECK(docks >= 0),
      is_installed boolean NOT NULL, is_renting boolean NOT NULL, is_returning boolean NOT NULL,
      is_demo boolean NOT NULL, PRIMARY KEY(station_id,reported_at,is_demo));
    CREATE INDEX ix_snapshots_latest ON availability_snapshots(station_id,reported_at DESC);
    """
    for statement in schema.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in (
        "availability_snapshots",
        "feed_sync_runs",
        "historical_demand",
        "trip_provenance",
        "historical_trips",
        "ingestion_runs",
        "station_aliases",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE stations DROP COLUMN is_demo")
