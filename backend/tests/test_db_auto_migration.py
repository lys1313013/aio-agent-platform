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
    monkeypatch.setattr(connection, "_db_schema_state", schema_state)
    monkeypatch.setattr(connection, "_run_alembic_upgrade", upgrade)

    await connection._auto_upgrade_schema(engine, {"new_head"})

    upgrade.assert_called_once_with()
    assert conn.execute.await_count == 2
    assert "pg_advisory_lock" in str(conn.execute.await_args_list[0].args[0])
    assert "pg_advisory_unlock" in str(conn.execute.await_args_list[1].args[0])
