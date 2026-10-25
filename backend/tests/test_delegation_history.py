"""History lookup is bounded and resolves names in one batch."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from aio_agent_platform.interface.routes.delegations import get_session_delegations


async def test_history_uses_one_name_query_and_stable_pagination(monkeypatch):
    sid, uid, parent, missing_child = [uuid4() for _ in range(4)]
    rows = [SimpleNamespace(id=uuid4(), parent_session_id=sid, parent_agent_id=parent,
        child_agent_id=missing_child, depth=1, task="work", context=None, status="done",
        result="ok", error=None, token_usage=None, duration_ms=1,
        created_at=datetime.now(UTC), completed_at=None) for _ in range(100)]
    queries = []
    async def execute(stmt):
        queries.append(stmt)
        if len(queries) == 1:
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
        return SimpleNamespace(all=lambda: [(parent, "parent")])
    monkeypatch.setattr("aio_agent_platform.interface.routes.delegations.require_agent_tenant", AsyncMock())
    session = SimpleNamespace(id=sid, agent_id=parent, source="chat")
    db = SimpleNamespace(scalar=AsyncMock(return_value=session), execute=execute)
    output = await get_session_delegations(sid, SimpleNamespace(id=uid), db, limit=100, offset=20)
    assert len(queries) == 2  # constant for 100 delegation rows
    params = queries[0].compile().params
    assert params["param_1"] == 100 and params["param_2"] == 20
    assert "delegations.created_at, delegations.id" in str(queries[0])
    assert all(row["parent_agent_name"] == "parent" and row["child_agent_name"] is None for row in output)


async def test_history_rejects_non_owner_before_fetching_records():
    db = SimpleNamespace(scalar=AsyncMock(return_value=None), execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await get_session_delegations(uuid4(), SimpleNamespace(id=uuid4()), db, limit=50, offset=0)
    assert error.value.status_code == 404
    db.execute.assert_not_awaited()


@pytest.fixture
async def authorized_history(tmp_path):
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles

    from aio_agent_platform.db.models import (
        Agent,
        Delegation,
        Session,
        Tenant,
        TenantMembership,
        User,
    )

    @compiles(JSONB, "sqlite")
    def sqlite_jsonb(_type, _compiler, **_kwargs):
        return "JSON"

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as conn:
        for model in (Agent, Delegation, Session, Tenant, TenantMembership, User):
            await conn.run_sync(model.__table__.create)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        actual = Tenant(id=uuid4(), name="actual", slug="actual")
        ui = Tenant(id=uuid4(), name="inactive UI", slug="ui", is_active=False)
        user = User(id=uuid4(), username="owner", email="owner@example.com", password_hash="x", tenant_id=ui.id)
        agent = Agent(id=uuid4(), name="agent", tenant_id=actual.id, created_by=user.id, visibility="private")
        session = Session(id=uuid4(), user_id=user.id, agent_id=agent.id)
        delegation = Delegation(id=uuid4(), parent_session_id=session.id, parent_agent_id=agent.id,
                                child_agent_id=uuid4(), user_id=user.id, depth=1, task="task", result="PRIVATE_RESULT")
        db.add_all([actual, ui, user, agent, session, delegation,
                    TenantMembership(user_id=user.id, tenant_id=actual.id)])
        await db.commit()
        yield SimpleNamespace(db=db, actual=actual, ui=ui, user=user, agent=agent, session=session, delegation=delegation)
    await engine.dispose()


async def _read_history(env, endpoint):
    from aio_agent_platform.interface.routes.delegations import get_delegation
    if endpoint == "list":
        return await get_session_delegations(env.session.id, env.user, env.db, limit=50, offset=0)
    return await get_delegation(env.delegation.id, env.user, env.db)


@pytest.mark.parametrize("endpoint", ["list", "detail"])
async def test_history_uses_actual_tenant_not_disabled_ui_selection(authorized_history, endpoint):
    env = authorized_history
    result = await _read_history(env, endpoint)
    assert (result[0] if endpoint == "list" else result)["result"] == "PRIVATE_RESULT"
    assert env.user.tenant_id == env.ui.id


@pytest.mark.parametrize("endpoint", ["list", "detail"])
@pytest.mark.parametrize("revocation", ["membership", "tenant", "private_agent", "unbound_session"])
async def test_history_denies_revoked_or_unknown_execution_scope(authorized_history, endpoint, revocation):
    from sqlalchemy import delete

    from aio_agent_platform.db.models import TenantMembership

    env = authorized_history
    if revocation == "membership":
        await env.db.execute(delete(TenantMembership).where(TenantMembership.user_id == env.user.id))
    elif revocation == "tenant":
        env.actual.is_active = False
    elif revocation == "private_agent":
        env.agent.created_by = uuid4()
    else:
        env.session.agent_id = None
    await env.db.flush()
    with pytest.raises(HTTPException) as error:
        await _read_history(env, endpoint)
    assert error.value.status_code == 403
