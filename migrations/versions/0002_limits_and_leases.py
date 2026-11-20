"""Per-application quotas and workflow run leases.

Revision ID: 0002_limits_and_leases
Revises: 0001_initial
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_limits_and_leases"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "biz"


def upgrade() -> None:
    # Quotas are opt-in: NULL means "no limit", so existing applications keep
    # behaving exactly as before.
    op.add_column(
        "applications",
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "applications",
        sa.Column("daily_request_quota", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "applications",
        sa.Column("max_concurrent_workflow_runs", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_applications_rate_limit_positive",
        "applications",
        "rate_limit_per_minute IS NULL OR rate_limit_per_minute > 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_applications_daily_quota_positive",
        "applications",
        "daily_request_quota IS NULL OR daily_request_quota > 0",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_applications_concurrency_positive",
        "applications",
        "max_concurrent_workflow_runs IS NULL OR max_concurrent_workflow_runs > 0",
        schema=SCHEMA,
    )

    # Lease columns let any replica adopt a run whose poller died.
    op.add_column(
        "workflow_runs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflow_runs_heartbeat",
        "workflow_runs",
        ["status", "heartbeat_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_heartbeat", table_name="workflow_runs", schema=SCHEMA)
    op.drop_column("workflow_runs", "heartbeat_at", schema=SCHEMA)

    op.drop_constraint(
        "ck_applications_concurrency_positive", "applications", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_applications_daily_quota_positive", "applications", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_applications_rate_limit_positive", "applications", schema=SCHEMA, type_="check"
    )
    op.drop_column("applications", "max_concurrent_workflow_runs", schema=SCHEMA)
    op.drop_column("applications", "daily_request_quota", schema=SCHEMA)
    op.drop_column("applications", "rate_limit_per_minute", schema=SCHEMA)
