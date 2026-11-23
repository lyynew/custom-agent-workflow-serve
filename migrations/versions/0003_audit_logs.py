"""Request audit trail for the business API.

Revision ID: 0003_audit_logs
Revises: 0002_limits_and_leases
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_audit_logs"
down_revision: str | None = "0002_limits_and_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "biz"


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("client_ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_logs_app_created",
        "audit_logs",
        ["app_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_audit_logs_app_status",
        "audit_logs",
        ["app_id", "status_code", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_app_status", table_name="audit_logs", schema=SCHEMA)
    op.drop_index("ix_audit_logs_app_created", table_name="audit_logs", schema=SCHEMA)
    op.drop_table("audit_logs", schema=SCHEMA)
