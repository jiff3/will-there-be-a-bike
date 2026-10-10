"""Explicit synthetic provenance for isolated offline replay.

Revision ID: 0006_demo_provenance
Revises: 0005_rider_features
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_demo_provenance"
down_revision = "0005_rider_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "replay_imports",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("replay_imports", "is_demo")
