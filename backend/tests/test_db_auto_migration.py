"""Startup database auto-migration tests."""

from unittest.mock import AsyncMock, Mock

import pytest

from aio_agent_platform.db import connection


class _ConnectionContext:
    def __init__(self, conn) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Engine:
    def __init__(self, conn) -> None:
        self.conn = conn

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self.conn)


@pytest.mark.asyncio
async def test_auto_upgrade_is_serialized_and_reaches_head(monkeypatch) -> None:
    conn = AsyncMock()
    engine = _Engine(conn)
    schema_state = AsyncMock(side_effect=["behind", "current"])
    upgrade = Mock()
    monkeypatch.setattr(connection, "_guard_legacy_tenant_upgrade", AsyncMock())
    monkeypatch.setattr(connection, "_db_schema_state", schema_state)
    monkeypatch.setattr(connection, "_run_alembic_upgrade", upgrade)

    await connection._auto_upgrade_schema(engine, {"new_head"})

    upgrade.assert_called_once_with()
    assert conn.execute.await_count == 2
    assert "pg_advisory_lock" in str(conn.execute.await_args_list[0].args[0])
    assert "pg_advisory_unlock" in str(conn.execute.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_ambiguous_history_blocks_upgrade_before_alembic(monkeypatch):
    conn = AsyncMock()
    engine = _Engine(conn)
    monkeypatch.setattr(connection, "_db_schema_state", AsyncMock(return_value="behind"))
    monkeypatch.setattr(connection, "_guard_legacy_tenant_upgrade", AsyncMock(side_effect=RuntimeError("historical ownership")))
    upgrade = Mock()
    monkeypatch.setattr(connection, "_run_alembic_upgrade", upgrade)
    with pytest.raises(RuntimeError, match="historical ownership"):
        await connection._auto_upgrade_schema(engine, {"head"})
    upgrade.assert_not_called()
    assert "pg_advisory_unlock" in str(conn.execute.await_args_list[-1].args[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["empty", "behind"])
async def test_new_and_existing_databases_share_upgrade_path(monkeypatch, state):
    engine = _Engine(AsyncMock())
    upgrade = AsyncMock()
    monkeypatch.setattr(connection, "get_engine", lambda: engine)
    monkeypatch.setattr(connection, "_local_alembic_heads", lambda: {"head"})
    monkeypatch.setattr(connection, "_db_schema_state", AsyncMock(return_value=state))
    monkeypatch.setattr(connection, "_auto_upgrade_schema", upgrade)
    await connection.init_db()
    upgrade.assert_awaited_once_with(engine, {"head"})


@pytest.mark.asyncio
async def test_force_bootstrap_cannot_stamp_unversioned_history(monkeypatch):
    engine = _Engine(AsyncMock())
    upgrade = AsyncMock()
    monkeypatch.setattr(connection.settings.db, "bootstrap_force", True)
    monkeypatch.setattr(connection, "get_engine", lambda: engine)
    monkeypatch.setattr(connection, "_local_alembic_heads", lambda: {"head"})
    monkeypatch.setattr(connection, "_db_schema_state", AsyncMock(return_value="legacy"))
    monkeypatch.setattr(connection, "_auto_upgrade_schema", upgrade)
    with pytest.raises(RuntimeError, match="未标记版本"):
        await connection.init_db()
    upgrade.assert_not_awaited()


@pytest.mark.postgres
@pytest.mark.parametrize('has_column', [True, False])
async def test_legacy_guard_does_not_backfill_unknown_tenants(engine, has_column):
    from uuid import uuid4

    from sqlalchemy import text
    schema = f"legacy_{uuid4().hex}"
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))
        try:
            await conn.execute(text(f'SET LOCAL search_path TO {schema}'))
            column = ', tenant_id UUID' if has_column else ''
            await conn.execute(text(f'CREATE TABLE cron_jobs (id INTEGER{column})'))
            await connection._guard_legacy_tenant_upgrade(conn)  # Empty is safe.
            await conn.execute(text('INSERT INTO cron_jobs (id) VALUES (1)'))
            with pytest.raises(RuntimeError, match="cron_jobs"):
                await connection._guard_legacy_tenant_upgrade(conn)
            assert await conn.scalar(text('SELECT count(*) FROM cron_jobs')) == 1
            if has_column:
                assert await conn.scalar(text('SELECT tenant_id FROM cron_jobs')) is None
        finally:
            await conn.execute(text('SET LOCAL search_path TO public'))
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


@pytest.mark.postgres
async def test_migration_preflight_releases_table_locks(engine, db_session, monkeypatch):
    import asyncio

    from sqlalchemy import text

    monkeypatch.setattr(connection, "_db_schema_state", AsyncMock(side_effect=["behind", "current"]))

    async def acquire_ddl_lock():
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '1s'"))
            await conn.execute(text("LOCK TABLE cron_jobs IN ACCESS EXCLUSIVE MODE"))

    def upgrade():
        asyncio.run(acquire_ddl_lock())

    monkeypatch.setattr(connection, "_run_alembic_upgrade", upgrade)
    await connection._auto_upgrade_schema(engine, {"head"})
