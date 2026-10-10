"""Metadata for the SQL-owned data tables; migrations remain the schema authority."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.persistence.base import Base

metadata = Base.metadata

station_aliases = Table(
    "station_aliases",
    metadata,
    Column("system_id", Uuid, ForeignKey("bike_share_systems.id"), primary_key=True),
    Column("alias", Text, primary_key=True),
    Column("station_id", Uuid, ForeignKey("stations.id"), nullable=False),
    Column("source", Text, nullable=False),
)
ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("source", Text, nullable=False),
    Column("checksum", Text, nullable=False),
    Column("scope", JSONB, nullable=False),
    Column("is_demo", Boolean, nullable=False),
    Column("state", Text, nullable=False),
    Column("rows_seen", BigInteger, CheckConstraint("rows_seen >= 0"), nullable=False),
    Column("invalid", BigInteger, CheckConstraint("invalid >= 0"), nullable=False),
    Column("errors", JSONB, nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
historical_trips = Table(
    "historical_trips",
    metadata,
    Column("ride_id", Text, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=False),
    Column("start_station", Text, nullable=False),
    Column("end_station", Text, nullable=False),
    Column("is_demo", Boolean, nullable=False),
    CheckConstraint("ended_at > started_at"),
    Index("ix_trips_departure", "start_station", "started_at"),
    Index("ix_trips_arrival", "end_station", "ended_at"),
)
trip_provenance = Table(
    "trip_provenance",
    metadata,
    Column("run_id", Text, ForeignKey("ingestion_runs.id"), primary_key=True),
    Column("ride_id", Text, ForeignKey("historical_trips.ride_id"), primary_key=True),
)
historical_demand = Table(
    "historical_demand",
    metadata,
    Column("run_id", Text, ForeignKey("ingestion_runs.id"), primary_key=True),
    Column("station_alias", Text, primary_key=True),
    Column("bucket_start", DateTime(timezone=True), primary_key=True),
    Column("departures", Integer, CheckConstraint("departures >= 0"), nullable=False),
    Column("arrivals", Integer, CheckConstraint("arrivals >= 0"), nullable=False),
    Index("ix_demand_station_time", "station_alias", "bucket_start"),
)
feed_sync_runs = Table(
    "feed_sync_runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("source", Text, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("feed_updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Text, nullable=False),
    Column("ttl", Integer, nullable=False),
    Column("is_demo", Boolean, nullable=False),
    Column("stations_seen", Integer, nullable=False),
    Column("rejected", Integer, nullable=False),
)
availability_snapshots = Table(
    "availability_snapshots",
    metadata,
    Column("station_id", Uuid, ForeignKey("stations.id"), primary_key=True),
    Column("reported_at", DateTime(timezone=True), primary_key=True),
    Column("feed_updated_at", DateTime(timezone=True), nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("sync_id", Uuid, ForeignKey("feed_sync_runs.id"), nullable=False),
    Column("bikes", Integer, CheckConstraint("bikes >= 0"), nullable=False),
    Column("docks", Integer, CheckConstraint("docks >= 0"), nullable=False),
    Column("is_installed", Boolean, nullable=False),
    Column("is_renting", Boolean, nullable=False),
    Column("is_returning", Boolean, nullable=False),
    Column("is_demo", Boolean, primary_key=True),
)
Index(
    "ix_snapshots_latest",
    availability_snapshots.c.station_id,
    availability_snapshots.c.reported_at.desc(),
)
Index("ix_snapshots_fetched_at", availability_snapshots.c.fetched_at)
Index("ix_snapshots_sync_id", availability_snapshots.c.sync_id)
Index("ix_feed_sync_fetched_at", feed_sync_runs.c.fetched_at)
