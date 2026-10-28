"""Database connection and session management."""

import asyncio
from collections.abc import AsyncGenerator
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from aio_agent_platform.core.config import settings

# Async engine
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None

# Optional user context for explicit background callers of get_db.
# HTTP authentication binds its verified identity directly to the request session.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
_RLS_USER_KEY = "aio_rls_user_id"
_SET_RLS_USER = text("SELECT set_config('app.current_user_id', :uid, true)")


def _set_transaction_user(
    session: Session, transaction: SessionTransaction, connection: Connection,
) -> None:
    """Restore a bound identity for each transaction, including after commit."""
    connection.execute(_SET_RLS_USER, {"uid": session.info[_RLS_USER_KEY]})


async def set_db_user(session: AsyncSession, user_id: UUID | str) -> None:
    """Bind an already authenticated user; this does not authorize tenant access."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.info[_RLS_USER_KEY] = str(user_id)
    if not event.contains(session.sync_session, "after_begin", _set_transaction_user):
        event.listen(session.sync_session, "after_begin", _set_transaction_user)
    # Authentication queries may already have opened this transaction, before
    # the listener was installed. Set its identity immediately as well.
    await session.execute(_SET_RLS_USER, {"uid": str(user_id)})


def get_engine() -> AsyncEngine:
    """Get or create the async engine singleton."""
    global _engine
    if _engine is None:
        # asyncpg 和 psycopg 使用不同的连接超时参数。
        driver = make_url(settings.db.url).get_driver_name()
        connect_args = {"connect_timeout": 8} if driver == "psycopg" else {"timeout": 8}
        _engine = create_async_engine(
            settings.db.url,
            echo=settings.server.log_level == "DEBUG",
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
            # 外部 DB 不可达时快速失败而非无限挂起：连接 + 连接池 checkout 各 8s 上限
            connect_args=connect_args,
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


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    FastAPI dependency: get a database session.

    Explicit background user context is supported; HTTP authentication sets the
    identity after this dependency yields. SQL settings are transaction-local.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            user_id = current_user_id.get(None)
            if user_id:
                await set_db_user(session, user_id)
            yield session
            await session.commit()
        except BaseException:
            # CancelledError (client disconnect / request cancelled) is NOT an
            # Exception; without rolling back here the session's transaction
            # stays open and the pooled connection idles forever, blocking DDL.
            await session.rollback()
            raise


def _local_alembic_heads() -> set[str]:
    """Require the shipped migration chain before initializing any database."""
    from alembic.script import ScriptDirectory

    alembic_dir = Path(__file__).resolve().parents[3] / "alembic"
    if not alembic_dir.is_dir():
        raise RuntimeError("缺少 Alembic 迁移目录，拒绝绕过版本管理创建数据库")
    heads = set(ScriptDirectory(dir=str(alembic_dir)).get_heads())
    if not heads:
        raise RuntimeError("Alembic 迁移链没有 head，无法确认数据库目标结构")
    return heads


async def _db_schema_state(conn, heads: set[str]) -> str:
    """Classify exact revision matches; unversioned tables are never an empty DB."""
    from aio_agent_platform.db.schema import database_schema_state

    return await conn.run_sync(database_schema_state, heads)


def _run_alembic_upgrade() -> None:
    """Run Alembic synchronously; callers execute this in a worker thread."""
    from alembic.config import Config

    from alembic import command

    backend_dir = Path(__file__).resolve().parents[3]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(config, "head")


async def _auto_upgrade_schema(engine: AsyncEngine, heads: set[str]) -> None:
    """Serialize startup migrations across app instances and upgrade to head."""
    import structlog

    log = structlog.get_logger()
    advisory_lock_id = 1_091_575_364  # Stable application-level migration lock.

    async with engine.connect() as lock_conn:
        await lock_conn.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": advisory_lock_id},
        )
        try:
            # Another instance may have completed the migration while this one waited.
            state = await _db_schema_state(lock_conn, heads)
            if state == "current":
                return
            if state == "legacy":
                raise RuntimeError("数据库包含未标记版本的表，拒绝自动迁移或 stamp head")
            await _guard_legacy_tenant_upgrade(lock_conn)
            # Release preflight read locks before Alembic uses a separate connection.
            # The session-level advisory lock survives this commit.
            await lock_conn.commit()
            log.warning("db_schema_outdated: automatically upgrading to alembic head")
            await asyncio.to_thread(_run_alembic_upgrade)
            if await _db_schema_state(lock_conn, heads) != "current":
                raise RuntimeError("数据库自动迁移完成后版本仍未到达 Alembic head")
            log.info("db_schema_auto_upgrade_complete", heads=sorted(heads))
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": advisory_lock_id},
            )


async def init_db() -> None:
    """Use the same versioned migrations for new and existing installations."""
    engine = get_engine()
    heads = _local_alembic_heads()
    async with engine.connect() as conn:
        state = await _db_schema_state(conn, heads)
    if state == "current":
        return
    if state == "legacy":
        raise RuntimeError(
            "数据库存在未标记版本的表，无法安全推断迁移起点；"
            "请先备份并审核实际结构，再制定显式迁移方案，禁止直接 stamp head"
        )
    # bootstrap_force deliberately cannot bypass revision checks or ownership guards.
    await _auto_upgrade_schema(engine, heads)


async def _guard_legacy_tenant_upgrade(conn) -> None:
    """Use the same ownership preflight as manual Alembic execution."""
    from aio_agent_platform.db.schema import require_known_tenant_ownership

    await conn.run_sync(require_known_tenant_ownership)


async def close_db() -> None:
    """Close database connections."""
    global _engine, _async_session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
