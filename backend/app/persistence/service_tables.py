"""Service metadata mirrors SQL-owned migration 0003; no create_all at runtime."""

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.persistence.base import Base

metadata = Base.metadata
users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(254), unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("is_admin", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
sessions = Table(
    "auth_sessions",
    metadata,
    Column("token_hash", CHAR(64), primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_hash", CHAR(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_sessions_expiry", sessions.c.expires_at)
Index("ix_sessions_user", sessions.c.user_id)
preferences = Table(
    "user_preferences",
    metadata,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "max_walk_meters",
        Integer,
        CheckConstraint("max_walk_meters BETWEEN 100 AND 3000"),
        nullable=False,
        server_default="1000",
    ),
    Column(
        "reliability_weight",
        Float,
        CheckConstraint("reliability_weight BETWEEN 0 AND 1"),
        nullable=False,
        server_default="0.7",
    ),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
limits = Table(
    "request_limits",
    metadata,
    Column("key", CHAR(64), primary_key=True),
    Column("window_start", DateTime(timezone=True), nullable=False),
    Column("attempts", Integer, CheckConstraint("attempts>0"), nullable=False),
)
Index("ix_request_limits_expiry", limits.c.window_start)
jobs = Table(
    "operation_jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("kind", Text, nullable=False),
    Column(
        "status",
        Text,
        CheckConstraint("status IN ('running','succeeded','failed','abandoned')"),
        nullable=False,
    ),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("details", JSONB, nullable=False, server_default=text("'{}'")),
)
Index("ix_jobs_kind_time", jobs.c.kind, jobs.c.started_at.desc())
Index("ix_jobs_started_at", jobs.c.started_at)
Index("ix_jobs_finished_at", jobs.c.finished_at)
cache = Table(
    "forecast_cache",
    metadata,
    Column("station_id", Text, primary_key=True),
    Column("model_version", Text, primary_key=True),
    Column("as_of", DateTime(timezone=True), primary_key=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
)
Index("ix_forecast_cache_expiry", cache.c.expires_at)
