"""Create PostGIS extension and foundational station tables.

Revision ID: 20261009_0001
Revises:
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa

from alembic import op

revision: str = "20261009_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "bike_share_systems",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "stations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=250), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "capacity IS NULL OR capacity >= 0", name="ck_stations_capacity_nonnegative"
        ),
        sa.ForeignKeyConstraint(["system_id"], ["bike_share_systems.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_id", "external_id", name="uq_stations_system_external_id"),
    )
    op.create_index(
        "ix_stations_location", "stations", ["location"], unique=False, postgresql_using="gist"
    )
    op.create_index(
        "ix_stations_system_active", "stations", ["system_id", "is_active"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_stations_system_active", table_name="stations")
    op.drop_index("ix_stations_location", table_name="stations", postgresql_using="gist")
    op.drop_table("stations")
    op.drop_table("bike_share_systems")
    op.execute("DROP EXTENSION IF EXISTS postgis")
