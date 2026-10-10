"""Additive rider/replay metadata; Alembic is the schema authority."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.persistence.base import Base

commutes = Table(
    "saved_commutes",
    Base.metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("ix_commutes_owner", commutes.c.user_id, commutes.c.created_at)
rules = Table(
    "availability_alerts",
    Base.metadata,
    Column("id", Uuid, primary_key=True),
    Column("commute_id", Uuid, ForeignKey("saved_commutes.id", ondelete="CASCADE"), nullable=False),
    Column("station_id", Uuid, ForeignKey("stations.id"), nullable=False),
    Column(
        "kind",
        String(20),
        CheckConstraint("kind IN ('pickup_empty','destination_full')"),
        nullable=False,
    ),
    Column(
        "mode",
        String(20),
        CheckConstraint("mode IN ('inventory','estimated_risk')"),
        nullable=False,
    ),
    Column(
        "threshold", Float, CheckConstraint("threshold >= 0 AND threshold <= 100"), nullable=False
    ),
    Column("lead_minutes", Integer, CheckConstraint("lead_minutes IN (30,60)"), nullable=False),
    Column(
        "cooldown_minutes",
        Integer,
        CheckConstraint("cooldown_minutes BETWEEN 15 AND 1440"),
        nullable=False,
    ),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("last_status", Text, nullable=False, server_default="not_checked"),
)
Index("ix_alerts_commute", rules.c.commute_id)
Index("ix_alerts_due", rules.c.enabled, rules.c.last_checked_at)
notifications = Table(
    "notifications",
    Base.metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("rule_id", Uuid, ForeignKey("availability_alerts.id", ondelete="SET NULL")),
    Column("scheduled_for", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("read_at", DateTime(timezone=True)),
    Column("title", Text, nullable=False),
    Column("message", Text, nullable=False),
    Column("evidence", JSONB, nullable=False),
    UniqueConstraint("rule_id", "scheduled_for", name="uq_notification_occurrence"),
)
Index("ix_notifications_owner_time", notifications.c.user_id, notifications.c.created_at.desc())
Index("ix_notifications_created", notifications.c.created_at)
Index("ix_notifications_rule_time", notifications.c.rule_id, notifications.c.created_at.desc())
replay_imports = Table(
    "replay_imports",
    Base.metadata,
    Column("id", Uuid, primary_key=True),
    Column("source_sha256", String(64), unique=True, nullable=False),
    Column("source_label", Text, nullable=False),
    Column("is_demo", Boolean, nullable=False, server_default=text("false")),
    Column("active", Boolean, nullable=False, server_default=text("false")),
    Column("first_bucket", DateTime(timezone=True), nullable=False),
    Column("last_bucket", DateTime(timezone=True), nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
Index("uq_replay_active", replay_imports.c.active, unique=True, postgresql_where=text("active"))
replay_bins = Table(
    "replay_bins",
    Base.metadata,
    Column(
        "import_id", Uuid, ForeignKey("replay_imports.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("station_id", Uuid, ForeignKey("stations.id"), primary_key=True),
    Column("bucket_start", DateTime(timezone=True), primary_key=True),
    Column("departures", Integer, CheckConstraint("departures >= 0")),
    Column("arrivals", Integer, CheckConstraint("arrivals >= 0")),
)
Index("ix_replay_time", replay_bins.c.import_id, replay_bins.c.bucket_start)
replay_predictions = Table(
    "replay_predictions",
    Base.metadata,
    Column("import_id", Uuid, primary_key=True),
    Column("station_id", Uuid, primary_key=True),
    Column("as_of", DateTime(timezone=True), primary_key=True),
    Column("model_version", Text, primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("computed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["import_id", "station_id", "as_of"],
        ["replay_bins.import_id", "replay_bins.station_id", "replay_bins.bucket_start"],
        ondelete="CASCADE",
    ),
)
