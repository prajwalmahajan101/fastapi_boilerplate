"""Alembic migration runner — async-aware.

Reads ``db_dsn`` from the application settings so migrations target the
same database the app does. Imports every ORM model (via ``src.db.tables``)
plus the separate ``api_log_metadata`` so autogenerate sees every table
under one combined ``target_metadata`` list.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Application metadata + model registry.
from src.common.settings import settings
from src.core.api_log.table import metadata as api_log_metadata
from src.core.base.model import BaseModel
from src.db import tables  # noqa: F401 — registers ORM models on BaseModel.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the live DSN so ``alembic.ini``'s empty ``sqlalchemy.url`` stays
# environment-agnostic. ``db_dsn`` is asyncpg-ready (e.g.
# ``postgresql+asyncpg://...``); Alembic's async engine pattern handles it.
if settings.db_dsn:
    config.set_main_option("sqlalchemy.url", settings.db_dsn)

# Combine both metadata objects — application ORM + audit log table.
target_metadata = [BaseModel.metadata, api_log_metadata]


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``--sql`` mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure Alembic against ``connection`` and run pending migrations.

    Alembic's ``context.configure`` is sync-only — async-aware setups
    pass this function to ``connection.run_sync`` so the sync Alembic
    machinery operates on a sync wrapper over the async connection.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine and dispatch ``do_run_migrations`` on it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database connection (async engine)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
