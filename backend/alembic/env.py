"""Alembic env.py — async PostgreSQL migration support."""

import asyncio
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Load .env before importing settings (pydantic-settings nested env_file bug workaround)
load_dotenv()

# Import our models and Base so alembic can detect schema changes
from aio_agent_platform.core.config import settings  # noqa: E402
from aio_agent_platform.db.models import Base  # noqa: E402
from aio_agent_platform.db.schema import (  # noqa: E402
    database_schema_state,
    require_known_tenant_ownership,
)

# Alembic Config object
config = context.config

# Override sqlalchemy.url from our settings (env vars take precedence)
config.set_main_option("sqlalchemy.url", settings.db.url)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with a connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        if database_schema_state(connection, set()) == "legacy":
            raise RuntimeError("数据库包含未标记版本的表，拒绝直接迁移或 stamp head")
        require_known_tenant_ownership(connection)
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
