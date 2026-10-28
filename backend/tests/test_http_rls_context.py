"""Exercise real HTTP authentication with a PostgreSQL role subject to RLS."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aio_agent_platform.auth.dependencies import CurrentUser, DbSession
from aio_agent_platform.auth.jwt_handler import create_access_token
from aio_agent_platform.db import connection
from aio_agent_platform.db.models import Base, MemoryVersion
from aio_agent_platform.interface.routes.memories import router

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def rls_http(engine, db_session, monkeypatch):
    role = f"http_rls_{uuid4().hex}"
    password = uuid4().hex
    users, memories, changes = [], [], []
    for _ in range(2):
        user_id, tenant_id, memory_id, change_id = (uuid4() for _ in range(4))
        users.append(user_id)
        memories.append(memory_id)
        changes.append(change_id)
        rows = {
            "tenants": {"id": tenant_id, "name": "RLS test", "slug": str(tenant_id)},
            "users": {
                "id": user_id, "tenant_id": tenant_id, "username": str(user_id),
                "email": f"{user_id}@test.invalid", "password_hash": "unused",
            },
            "tenant_memberships": {"user_id": user_id, "tenant_id": tenant_id},
            "memories": {
                "id": memory_id, "user_id": user_id, "tenant_id": tenant_id,
                "layer": "L1", "content": "RLS test", "version": 1,
            },
            "memory_changes": {
                "id": change_id, "user_id": user_id, "kind": "test", "before": {}, "after": {},
            },
            "memory_versions": {
                "memory_id": memory_id, "version": 1, "user_id": user_id,
                "change_id": change_id, "kind": "test",
                "snapshot": {"agent_id": None, "layer": "L1"},
            },
        }
        for table, row in rows.items():
            await db_session.execute(Base.metadata.tables[table].insert().values(**row))
    await db_session.commit()

    async with engine.begin() as admin:
        await admin.execute(text(
            f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD \'{password}\''
        ))
        await admin.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        await admin.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
    # Use a real non-owner login: a SET ROLE issued by a connect hook can itself
    # be rolled back, accidentally returning to the test database's owner role.
    runtime = create_async_engine(
        engine.url.set(username=role, password=password), pool_size=1, max_overflow=0,
    )

    monkeypatch.setattr(connection, "_async_session_factory", async_sessionmaker(
        runtime, expire_on_commit=False,
    ))
    app = FastAPI()
    app.include_router(router)
    cancel_started = asyncio.Event()

    @app.get("/test/transactions")
    async def transactions(user: CurrentUser, db: DbSession):
        # No user filter: these reads must be protected by PostgreSQL itself.
        query = select(MemoryVersion.memory_id)
        before = list(await db.scalars(query))
        await db.commit()
        after_commit = list(await db.scalars(query))
        await db.rollback()
        after_rollback = list(await db.scalars(query))
        async with db.begin_nested() as savepoint:
            inside_savepoint = list(await db.scalars(query))
            await savepoint.rollback()
        after_savepoint = list(await db.scalars(query))
        return [before, after_commit, after_rollback, inside_savepoint, after_savepoint]

    @app.get("/test/error")
    async def error(user: CurrentUser, db: DbSession):
        assert list(await db.scalars(select(MemoryVersion.user_id))) == [user.id]
        raise RuntimeError("abort authenticated request")

    @app.get("/test/cancel")
    async def cancel(user: CurrentUser, db: DbSession):
        assert list(await db.scalars(select(MemoryVersion.user_id))) == [user.id]
        cancel_started.set()
        await asyncio.Event().wait()

    @app.get("/test/anonymous")
    async def anonymous(db: DbSession):
        identity = await db.scalar(text("SELECT current_setting('app.current_user_id', true)"))
        return {"identity": identity, "versions": list(await db.scalars(select(MemoryVersion.memory_id)))}

    previous_context = connection.current_user_id.set(None)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
        ) as client:
            yield SimpleNamespace(
                client=client, users=users, memories=memories, changes=changes,
                cancel_started=cancel_started,
            )
    finally:
        connection.current_user_id.reset(previous_context)
        await runtime.dispose()
        async with engine.begin() as admin:
            await admin.execute(text(f'DROP OWNED BY "{role}"'))
            await admin.execute(text(f'DROP ROLE "{role}"'))


def auth(user_id):
    return {"Authorization": f"Bearer {create_access_token(user_id, 'user')}"}


async def test_http_history_is_visible_only_to_its_authenticated_owner(rls_http):
    env = rls_http
    for index, user_id in enumerate(env.users):
        response = await env.client.get("/api/memories/changes", headers=auth(user_id))
        assert response.status_code == 200
        assert [row["id"] for row in response.json()] == [str(env.changes[index])]
        response = await env.client.get(f"/api/memories/{env.memories[index]}/versions", headers=auth(user_id))
        assert response.status_code == 200
        assert [row["version"] for row in response.json()["versions"]] == [1]
        other = env.memories[1 - index]
        response = await env.client.get(f"/api/memories/{other}/versions", headers=auth(user_id))
        assert response.status_code == 404
        assert connection.current_user_id.get() is None


async def test_http_rls_identity_survives_transaction_boundaries(rls_http):
    env = rls_http
    for index, user_id in enumerate(env.users):
        response = await env.client.get("/test/transactions", headers=auth(user_id))
        assert response.status_code == 200
        assert response.json() == [[str(env.memories[index])]] * 5


@pytest.mark.parametrize("outcome", ["commit", "exception", "cancel"])
async def test_http_identity_does_not_leak_into_reused_connection_or_context(rls_http, outcome):
    env = rls_http
    if outcome == "cancel":
        request = asyncio.create_task(env.client.get("/test/cancel", headers=auth(env.users[0])))
        try:
            await asyncio.wait_for(env.cancel_started.wait(), 5)
        finally:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
    else:
        endpoint = "/test/error" if outcome == "exception" else "/api/memories/changes"
        response = await env.client.get(endpoint, headers=auth(env.users[0]))
        assert response.status_code == (500 if outcome == "exception" else 200)
    assert connection.current_user_id.get() is None
    # pool_size=1 forces the next request to use the same physical connection.
    response = await env.client.get("/test/anonymous")
    assert response.status_code == 200
    assert response.json()["identity"] in (None, "")
    assert response.json()["versions"] == []
    response = await env.client.get("/api/memories/changes", headers=auth(env.users[1]))
    assert [row["id"] for row in response.json()] == [str(env.changes[1])]
