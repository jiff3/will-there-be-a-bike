"""Bounded retention and operational reads need timestamp/FK indexes.

Revision ID: 0004_retention_indexes
Revises: 0003_service
"""

from alembic import op

revision = "0004_retention_indexes"
down_revision = "0003_service"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, table, columns in (
        ("ix_snapshots_fetched_at", "availability_snapshots", ["fetched_at"]),
        ("ix_snapshots_sync_id", "availability_snapshots", ["sync_id"]),
        ("ix_feed_sync_fetched_at", "feed_sync_runs", ["fetched_at"]),
        ("ix_jobs_finished_at", "operation_jobs", ["finished_at"]),
        ("ix_jobs_started_at", "operation_jobs", ["started_at"]),
    ):
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table in (
        ("ix_jobs_started_at", "operation_jobs"),
        ("ix_jobs_finished_at", "operation_jobs"),
        ("ix_feed_sync_fetched_at", "feed_sync_runs"),
        ("ix_snapshots_sync_id", "availability_snapshots"),
        ("ix_snapshots_fetched_at", "availability_snapshots"),
    ):
        op.drop_index(name, table_name=table)
