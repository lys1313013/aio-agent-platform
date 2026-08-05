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
            # 外部 DB 不可达时快速失败而非无限挂起：连接 + 连接池 checkout 各 8s 上限
            connect_args={"timeout": 8},
            pool_timeout=8,
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
    # DDL 需要 ACCESS EXCLUSIVE 锁，若被其他长事务/残留连接阻塞会无限等锁。
    # 限定锁等待 10s，超时即放弃让启动继续（迁移幂等，下次启动可重试）。
    await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
    migrations = [
        # Tenant isolation. Existing installations are moved into one default tenant
        # so their current sharing behaviour is preserved after upgrading.
        """CREATE TABLE IF NOT EXISTS tenants (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name VARCHAR(128) NOT NULL,
            slug VARCHAR(64) NOT NULL UNIQUE,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        """INSERT INTO tenants (id, name, slug, is_active, created_at, updated_at)
           VALUES (
               '00000000-0000-0000-0000-000000000001',
               '默认租户',
               'default',
               true,
               now(),
               now()
           )
           ON CONFLICT (slug) DO NOTHING""",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE users SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE users ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_users_tenant ON users (tenant_id)",
        """CREATE TABLE IF NOT EXISTS tenant_memberships (
            tenant_id UUID NOT NULL,
            user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        )""",
        """INSERT INTO tenant_memberships (tenant_id, user_id)
           SELECT tenant_id, id FROM users
           ON CONFLICT (tenant_id, user_id) DO NOTHING""",
        """CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user
           ON tenant_memberships (user_id)""",
        """UPDATE users SET role = 'superadmin'
           WHERE id = (
               SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1
           )
           AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'superadmin')""",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE agents a SET tenant_id = u.tenant_id
           FROM users u WHERE a.created_by = u.id AND a.tenant_id IS NULL""",
        """UPDATE agents SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE agents ALTER COLUMN tenant_id SET NOT NULL",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS visibility VARCHAR(16) NOT NULL DEFAULT 'tenant'",
        "CREATE INDEX IF NOT EXISTS idx_agents_tenant_visibility ON agents (tenant_id, visibility)",
        "CREATE INDEX IF NOT EXISTS idx_agents_creator ON agents (created_by)",
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE knowledge_bases SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE knowledge_bases ALTER COLUMN tenant_id SET NOT NULL",
        "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE mcp_servers SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE mcp_servers ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_mcp_servers_tenant ON mcp_servers (tenant_id)",
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS created_by UUID",
        """UPDATE knowledge_bases SET created_by = (
               SELECT id FROM users ORDER BY created_at LIMIT 1
           ) WHERE created_by IS NULL""",
        "ALTER TABLE knowledge_bases ALTER COLUMN created_by SET NOT NULL",
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS visibility VARCHAR(16) NOT NULL DEFAULT 'tenant'",
        """CREATE INDEX IF NOT EXISTS idx_knowledge_bases_tenant_visibility
           ON knowledge_bases (tenant_id, visibility)""",
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
