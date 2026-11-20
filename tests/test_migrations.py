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

    assert version == "0001_initial"
    # Aegra owns public.alembic_version; this migration must not create it.
    assert public_version_table is None
