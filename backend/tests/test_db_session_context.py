"""Exercise transaction-local user context on both supported PostgreSQL drivers.

Only session settings and SELECTs are used: no tables are created or modified.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from aio_agent_platform.core.config import settings
from aio_agent_platform.db import connection


@pytest.mark.postgres
@pytest.mark.parametrize("driver", ["asyncpg", "psycopg"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
async def test_user_context_does_not_leak_after_transaction(monkeypatch, driver, rollback):
    url = make_url(settings.db.url).set(drivername=f"postgresql+{driver}")
    monkeypatch.setattr(settings.db, "url", url.render_as_string(hide_password=False))
    monkeypatch.setattr(connection, "_engine", None)
    monkeypatch.setattr(connection, "_async_session_factory", None)
    engine = connection.get_engine()
    user_id = str(uuid4())
    token = connection.current_user_id.set(user_id)
    generator = connection.get_db()
    try:
        session = await anext(generator)
        query = text("SELECT current_setting('app.current_user_id', true), pg_backend_pid()")
        value, backend_pid = (await session.execute(query)).one()
        assert value == user_id

        if rollback:
            with pytest.raises(RuntimeError, match="abort transaction"):
                await generator.athrow(RuntimeError("abort transaction"))
        else:
            with pytest.raises(StopAsyncIteration):
                await anext(generator)

        # Read on the returned physical connection, without setting a new user.
        factory = connection.get_session_factory()
        async with factory() as next_session:
            value, reused_pid = (await next_session.execute(query)).one()
            assert reused_pid == backend_pid
            assert value in (None, "")
    finally:
        await generator.aclose()
        connection.current_user_id.reset(token)
        await engine.dispose()
