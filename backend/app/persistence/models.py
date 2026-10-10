from __future__ import annotations

import uuid
from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.persistence.base import Base


class BikeShareSystem(Base):
    __tablename__ = "bike_share_systems"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stations: Mapped[list[Station]] = relationship(
        back_populates="system", cascade="all, delete-orphan"
    )


class Station(Base):
    __tablename__ = "stations"
    __table_args__ = (
        UniqueConstraint("system_id", "external_id", name="uq_stations_system_external_id"),
        CheckConstraint(
            "capacity IS NULL OR capacity >= 0", name="ck_stations_capacity_nonnegative"
        ),
        Index("ix_stations_location", "location", postgresql_using="gist"),
        Index("ix_stations_system_active", "system_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    system_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bike_share_systems.id", ondelete="CASCADE")
    )
    external_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(250))
    capacity: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[object] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    system: Mapped[BikeShareSystem] = relationship(back_populates="stations")
