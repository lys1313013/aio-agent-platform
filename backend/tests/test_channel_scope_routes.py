"""Channel creation/editing is tenant-wide; identity bindings stay channel-local."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aio_agent_platform.auth.dependencies import get_current_user
from aio_agent_platform.channels.binding import issue_bind_code
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent, ChannelBinding, User
from aio_agent_platform.interface.routes import channels


@pytest.mark.asyncio
async def test_regular_user_channel_management_and_binding_scope(db_session, monkeypatch):
    tenant_id = uuid4()
    user = User(
        tenant_id=tenant_id,
        username="channel_member",
        email="channel_member@test.com",
        password_hash="x",
        role="user",
    )
    agent = Agent(tenant_id=tenant_id, name="channel_agent", created_by=uuid4())
    db_session.add_all([user, agent])
    await db_session.flush()
    app = FastAPI()
    app.include_router(channels.router)
    app.include_router(channels.user_router)
    app.state.channel_connection_manager = SimpleNamespace(start_channel=AsyncMock())
    app.dependency_overrides[get_current_user] = lambda: user

    async def database():
        yield db_session

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(channels, "_verify_channel_credentials", AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "name": "first",
            "agent_id": str(agent.id),
            "app_id": "app_first",
            "app_secret": "test",
            "mode": "websocket",
        }
        first = await client.post("/api/channels", json=payload)
        assert first.status_code == 201, first.text
        first_id = first.json()["id"]
        second = await client.post(
            "/api/channels", json={**payload, "name": "second", "app_id": "app_second"}
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["id"]
        result = await client.put(f"/api/channels/{first_id}", json={"name": "renamed"})
        assert result.status_code == 200 and result.json()["name"] == "renamed"
        assert len((await client.get("/api/channels")).json()) == 2
        # Activating/deleting existing channels still requires an admin.
        assert (await client.post(f"/api/channels/{first_id}/disable")).status_code == 403
        assert (await client.delete(f"/api/channels/{first_id}")).status_code == 403
        from uuid import UUID

        code, _ = await issue_bind_code(db_session, UUID(first_id), "external_member", tenant_id)
        wrong = await client.post(
            "/api/channel-bindings/bind", json={"channel_id": second_id, "code": code}
        )
        assert wrong.status_code == 400
        bound = await client.post(
            "/api/channel-bindings/bind", json={"channel_id": first_id, "code": code}
        )
        assert bound.status_code == 200, bound.text
        own = (await client.get(f"/api/channels/{first_id}/bindings")).json()
        assert len(own) == 1 and own[0]["channel_id"] == first_id
        assert (await client.get(f"/api/channels/{second_id}/bindings")).json() == []
        other = ChannelBinding(
            tenant_id=tenant_id, channel_id=UUID(first_id), external_id="other", user_id=uuid4()
        )
        db_session.add(other)
        await db_session.flush()
        assert len((await client.get(f"/api/channels/{first_id}/bindings")).json()) == 1
        assert (await client.delete(f"/api/channel-bindings/{other.id}")).status_code == 404
        assert (await client.delete(f"/api/channel-bindings/{own[0]['id']}")).status_code == 204
        # Tenant boundaries apply even after opening management to ordinary users.
        user.tenant_id = uuid4()
        await db_session.flush()
        assert (await client.get("/api/channels")).json() == []
        assert (
            await client.put(f"/api/channels/{first_id}", json={"name": "forbidden"})
        ).status_code == 404
        assert (await client.get(f"/api/channels/{first_id}/bindings")).status_code == 404


@pytest.mark.asyncio
async def test_migration_restores_only_unambiguous_channel_sources(engine):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration = (
        Path(__file__).parents[1] / "alembic/versions/c9d0e1f2a3b4_restore_channel_binding_scope.py"
    )
    spec = importlib.util.spec_from_file_location("channel_scope_migration", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("CREATE SCHEMA binding_migration_test"))
            await connection.execute(text("SET LOCAL search_path TO binding_migration_test"))
            await connection.execute(
                text(
                    "CREATE TABLE channel_bindings (id uuid PRIMARY KEY, tenant_id uuid, external_id text, user_id uuid, CONSTRAINT uq_channel_binding_external UNIQUE (tenant_id, external_id))"
                )
            )
            await connection.execute(
                text("CREATE TABLE channel_configs (id uuid PRIMARY KEY, tenant_id uuid)")
            )
            await connection.execute(
                text(
                    "CREATE TABLE channel_bind_codes (tenant_id uuid, external_id text, channel_id uuid, used_by uuid, used_at timestamptz)"
                )
            )
            tenant, user, channel_a, channel_b = uuid4(), uuid4(), uuid4(), uuid4()
            for channel in (channel_a, channel_b):
                await connection.execute(
                    text("INSERT INTO channel_configs VALUES (:id, :tenant)"),
                    {"id": channel, "tenant": tenant},
                )
            for identity in ("known", "unknown", "ambiguous"):
                await connection.execute(
                    text("INSERT INTO channel_bindings VALUES (:id, :tenant, :external, :user)"),
                    {"id": uuid4(), "tenant": tenant, "external": identity, "user": user},
                )
            for identity, channel in [
                ("known", channel_a),
                ("ambiguous", channel_a),
                ("ambiguous", channel_b),
            ]:
                await connection.execute(
                    text(
                        "INSERT INTO channel_bind_codes VALUES (:tenant, :external, :channel, :user, now())"
                    ),
                    {"tenant": tenant, "external": identity, "channel": channel, "user": user},
                )

            def upgrade(conn):
                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()

            await connection.run_sync(upgrade)
            records = dict(
                (
                    await connection.execute(
                        text("SELECT external_id, channel_id FROM channel_bindings")
                    )
                ).all()
            )
            assert records == {"known": channel_a, "unknown": None, "ambiguous": None}
        finally:
            await transaction.rollback()
