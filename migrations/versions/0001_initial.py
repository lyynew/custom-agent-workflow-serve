"""Initial business schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "biz"


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("app_name", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.Text(), server_default=sa.text("'jwt'"), nullable=False),
        sa.Column("jwt_config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "allowed_agents",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "allowed_workflows",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("app_id", name="pk_applications"),
        schema=SCHEMA,
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.UniqueConstraint("thread_id", name="uq_conversations_thread_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversations_scope",
        "conversations",
        ["app_id", "user_id", "updated_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            [f"{SCHEMA}.conversations.id"],
            name="fk_conversation_messages_conversation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_messages"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_messages_conversation_created",
        "conversation_messages",
        ["conversation_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("aegra_run_id", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=True),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflow_runs_scope",
        "workflow_runs",
        ["app_id", "user_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"], schema=SCHEMA)

    op.create_table(
        "model_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("temperature", sa.Float(), server_default=sa.text("0.7"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("60"), nullable=False),
        sa.Column("extra", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_configs"),
        sa.UniqueConstraint("name", name="uq_model_configs_name"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("model_configs", schema=SCHEMA)
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs", schema=SCHEMA)
    op.drop_index("ix_workflow_runs_scope", table_name="workflow_runs", schema=SCHEMA)
    op.drop_table("workflow_runs", schema=SCHEMA)
    op.drop_index("ix_messages_conversation_created", table_name="conversation_messages", schema=SCHEMA)
    op.drop_table("conversation_messages", schema=SCHEMA)
    op.drop_index("ix_conversations_scope", table_name="conversations", schema=SCHEMA)
    op.drop_table("conversations", schema=SCHEMA)
    op.drop_table("applications", schema=SCHEMA)
