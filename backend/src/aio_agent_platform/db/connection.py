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
        except BaseException:
            # CancelledError (client disconnect / request cancelled) is NOT an
            # Exception; without rolling back here the session's transaction
            # stays open and the pooled connection idles forever, blocking DDL.
            await session.rollback()
            raise


def _local_alembic_heads() -> set[str]:
    """读取本地 alembic 迁移的 head 版本(纯文件解析,不连库)。

    找不到 alembic 目录时返回空集合,调用方退化为哨兵表检测。
    """
    from pathlib import Path

    try:
        from alembic.script import ScriptDirectory

        alembic_dir = Path(__file__).resolve().parents[3] / "alembic"
        if not alembic_dir.is_dir():
            return set()
        return set(ScriptDirectory(dir=str(alembic_dir)).get_heads())
    except Exception:
        return set()


async def _schema_initialized(conn) -> bool:
    """哨兵检测:核心表都在即认为 schema 已完成初始化(legacy 库兜底用)。

    覆盖早期(users)与后期(tenant_memberships / llm_models)迁移的表,
    单次查询一个网络往返。
    """
    result = await conn.execute(
        text(
            "SELECT to_regclass('public.users'), to_regclass('public.tenants'),"
            " to_regclass('public.tenant_memberships'),"
            " to_regclass('public.llm_models'), to_regclass('public.cron_jobs')"
        )
    )
    return all(row is not None for row in result.one())


async def _db_schema_state(conn, heads: set[str]) -> str:
    """判断数据库 schema 状态: current / behind / legacy / empty。"""
    result = await conn.execute(text("SELECT to_regclass('public.alembic_version')"))
    if result.scalar() is not None:
        rows = (
            (await conn.execute(text("SELECT version_num FROM alembic_version")))
            .scalars()
            .all()
        )
        if rows and heads and all(rev in heads for rev in rows):
            return "current"
        return "behind" if heads else "current"
    # 无版本记录:历史上由 create_all bootstrap 出来的库,哨兵表兜底识别
    if await _schema_initialized(conn):
        return "legacy"
    return "empty"


async def _stamp_alembic_head(conn, heads: set[str]) -> None:
    """bootstrap 完成后写入 alembic 版本,使后续启动走版本比对快路径。"""
    await conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version ("
            "version_num VARCHAR(32) NOT NULL,"
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )
    )
    await conn.execute(text("DELETE FROM alembic_version"))
    for rev in heads:
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": rev},
        )


async def init_db() -> None:
    """Initialize database: create extensions and all tables (dev bootstrap)."""
    import structlog

    log = structlog.get_logger()
    engine = get_engine()
    async with engine.begin() as conn:
        # 远程库上 create_all(checkfirst) 每张表一次往返(53 张)+ 60 余条
        # 幂等手动迁移,共 110+ 次往返,启动多花 10s 级。已初始化的库直接跳过;
        # 后续 schema 变更走 alembic。全新部署时走完整 bootstrap 并盖章版本,
        # 也可用 DATABASE_BOOTSTRAP_FORCE=true 强制执行。
        heads = _local_alembic_heads()
        if not settings.db.bootstrap_force:
            state = await _db_schema_state(conn, heads)
            if state == "current":
                return
            if state == "behind":
                # 启动时绝不自动迁移(可能是远程生产库),只告警
                log.warning(
                    "db_schema_outdated: run 'uv run alembic upgrade head'",
                )
                return
            if state == "legacy":
                log.warning(
                    "db_schema_unstamped: 库由旧版 bootstrap 创建,无 alembic 版本记录;"
                    "确认 schema 最新后请执行 'uv run alembic stamp head'",
                )
                return

        # Create extensions
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))

        # Import all models to register them with Base
        from aio_agent_platform.db.models import Base

        # Create all tables (idempotent — skips existing tables)
        await conn.run_sync(Base.metadata.create_all)

        # Manual migrations for existing tables that need new columns
        await _run_manual_migrations(conn)

        # bootstrap 出来的库即当前 schema,写入 alembic 头版本
        if heads:
            await _stamp_alembic_head(conn, heads)


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
        # Portrait tenant isolation
        "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE user_profiles up SET tenant_id = u.tenant_id
           FROM users u WHERE up.user_id = u.id AND up.tenant_id IS NULL""",
        """UPDATE user_profiles SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE user_profiles ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_user_profiles_tenant ON user_profiles (tenant_id)",
        "ALTER TABLE portrait_versions ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE portrait_versions pv SET tenant_id = u.tenant_id
           FROM users u WHERE pv.user_id = u.id AND pv.tenant_id IS NULL""",
        """UPDATE portrait_versions SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE portrait_versions ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_portrait_versions_tenant ON portrait_versions (tenant_id)",
        # UserProfile: composite primary key (user_id, tenant_id) for multi-tenant profiles
        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'user_profiles_pkey'
                  AND conrelid = 'user_profiles'::regclass
                  AND pg_get_constraintdef(oid) LIKE 'PRIMARY KEY (user_id)%'
                  AND pg_get_constraintdef(oid) NOT LIKE '%tenant_id%'
            ) THEN
                ALTER TABLE user_profiles DROP CONSTRAINT user_profiles_pkey;
                ALTER TABLE user_profiles ADD PRIMARY KEY (user_id, tenant_id);
            END IF;
        END $$""",
        # CronJob: agent_id and message columns
        "ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS agent_id UUID",
        "ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS message TEXT",
        # Cron job tenant isolation
        "ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE cron_jobs cj SET tenant_id = u.tenant_id
           FROM users u WHERE cj.user_id = u.id AND cj.tenant_id IS NULL""",
        """UPDATE cron_jobs SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE cron_jobs ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_cron_jobs_tenant ON cron_jobs (tenant_id)",
        "ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "ALTER TABLE cron_job_runs ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE cron_job_runs cr SET tenant_id = u.tenant_id
           FROM users u WHERE cr.user_id = u.id AND cr.tenant_id IS NULL""",
        """UPDATE cron_job_runs SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE cron_job_runs ALTER COLUMN tenant_id SET NOT NULL",
        # LLM tenant isolation
        "ALTER TABLE llm_providers ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE llm_providers SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE llm_providers ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_llm_providers_tenant ON llm_providers (tenant_id)",
        "ALTER TABLE llm_models ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE llm_models SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE llm_models ALTER COLUMN tenant_id SET NOT NULL",
        """CREATE INDEX IF NOT EXISTS idx_llm_models_tenant_default
           ON llm_models (tenant_id, is_default) WHERE is_default""",
        # Memory tenant isolation
        "ALTER TABLE memories ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE memories m SET tenant_id = u.tenant_id
           FROM users u WHERE m.user_id = u.id AND m.tenant_id IS NULL""",
        """UPDATE memories SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE memories ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_memories_tenant ON memories (tenant_id)",
        "ALTER TABLE daily_memories ADD COLUMN IF NOT EXISTS tenant_id UUID",
        """UPDATE daily_memories dm SET tenant_id = u.tenant_id
           FROM users u WHERE dm.user_id = u.id AND dm.tenant_id IS NULL""",
        """UPDATE daily_memories SET tenant_id = '00000000-0000-0000-0000-000000000001'
           WHERE tenant_id IS NULL""",
        "ALTER TABLE daily_memories ALTER COLUMN tenant_id SET NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_daily_memories_tenant ON daily_memories (tenant_id)",
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
