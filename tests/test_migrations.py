"""Schema and migration isolation checks."""

from sqlalchemy import text

from app.database.session import get_session_factory


async def test_business_tables_exist_in_biz_schema():
    async with get_session_factory()() as session:
        rows = await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'biz'")
        )
        tables = {row[0] for row in rows}

    assert {
        "applications",
        "conversations",
        "conversation_messages",
        "workflow_runs",
        "model_configs",
        "biz_alembic_version",
    } <= tables


async def test_business_migration_uses_its_own_version_table():
    async with get_session_factory()() as session:
        version = await session.scalar(text("SELECT version_num FROM biz.biz_alembic_version"))
        public_version_table = await session.scalar(text("SELECT to_regclass('public.alembic_version')"))

    # Schema isolation is the invariant; the exact revision is not.
    assert version
    # Aegra owns public.alembic_version; our migrations must not create it.
    assert public_version_table is None


async def test_quota_columns_exist():
    async with get_session_factory()() as session:
        rows = await session.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'biz' AND table_name = 'applications'
                """
            )
        )
        application_columns = {row[0] for row in rows}

        heartbeat = await session.scalar(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'biz' AND table_name = 'workflow_runs'
                  AND column_name = 'heartbeat_at'
                """
            )
        )

    assert {"rate_limit_per_minute", "daily_request_quota", "max_concurrent_workflow_runs"} <= (
        application_columns
    )
    assert heartbeat == 1
