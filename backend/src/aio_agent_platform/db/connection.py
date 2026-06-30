"""Database connection and session management."""

from collections.abc import AsyncGenerator
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aio_agent_platform.core.config import settings

# Async engine
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None

# Current user ID for RLS (set by get_current_user dependency)
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def get_engine() -> AsyncEngine:
    """Get or create the async engine singleton."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.db.url,
            echo=settings.server.log_level == "DEBUG",
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory singleton."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: get a database session.

    Also sets the PostgreSQL session variable for RLS:
    SET LOCAL app.current_user_id = '<user_id>'
    """
    factory = get_session_factory()
    async with factory() as session:
        user_id = current_user_id.get(None)
        if user_id:
            # SET LOCAL only affects the current transaction, auto-cleared on commit
            await session.execute(
                text("SET LOCAL app.current_user_id = :uid"),
                {"uid": user_id},
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database: create extensions and all tables (dev bootstrap)."""
    engine = get_engine()
    async with engine.begin() as conn:
        # Create extensions
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))

        # Import all models to register them with Base
        from aio_agent_platform.db.models import Base

        # Create all tables (idempotent — skips existing tables)
        await conn.run_sync(Base.metadata.create_all)

        # Manual migrations for existing tables that need new columns
        await _run_manual_migrations(conn)


async def _run_manual_migrations(conn) -> None:
    """Idempotent column additions for existing tables."""
    migrations = [
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS personal_portrait TEXT",
        # Portrait version history table
        """CREATE TABLE IF NOT EXISTS portrait_versions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id UUID NOT NULL,
            content TEXT,
            source VARCHAR(16) NOT NULL DEFAULT 'manual',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        "CREATE INDEX IF NOT EXISTS idx_portrait_versions_user ON portrait_versions (user_id, created_at)",
        # CronJob: agent_id and message columns
        "ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS agent_id UUID",
        "ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS message TEXT",
    ]
    for sql in migrations:
        await conn.execute(text(sql))


async def close_db() -> None:
    """Close database connections."""
    global _engine, _async_session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
