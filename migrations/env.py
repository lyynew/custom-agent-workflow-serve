"""Alembic environment for business tables.

Aegra owns `public.alembic_version`. This configuration deliberately uses
`biz.biz_alembic_version` so the two migration histories cannot overwrite each
other.
"""

import asyncio
import threading
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings
from app.models import Base  # noqa: F401 - imports every model for metadata

config = context.config

if config.config_file_name is not None and threading.current_thread() is threading.main_thread():
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return (
        config.attributes.get("sqlalchemy.url")
        or config.get_main_option("sqlalchemy.url")
        or get_settings().database_url
    )


def do_run_migrations(connection) -> None:
    # The version table lives in `biz`, so the schema must exist first.
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS biz"))
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table="biz_alembic_version",
        version_table_schema="biz",
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table="biz_alembic_version",
        version_table_schema="biz",
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
