"""Ownership enforcement with an isolated SQLite DB, never application data."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from aio_agent_platform.auth.dependencies import get_execution_user
from aio_agent_platform.core.execution_scope import ExecutionAccessError
from aio_agent_platform.cron_jobs.service import CronJobService
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import CronJob, CronJobRun, Tenant, TenantMembership, User
from aio_agent_platform.interface.routes.cron_jobs import router


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cron.db'}")
    async with engine.begin() as conn:
        for model in (Tenant, User, TenantMembership, CronJob, CronJobRun):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        tenant = Tenant(id=uuid4(), name="execution", slug="execution")
        inactive_ui = Tenant(id=uuid4(), name="inactive ui", slug="inactive-ui", is_active=False)
        owner = User(id=uuid4(), username="owner", email="owner@example.com", password_hash="x", tenant_id=inactive_ui.id)
        attacker = User(id=uuid4(), username="attacker", email="attacker@example.com", password_hash="x", tenant_id=tenant.id)
        job = CronJob(id=uuid4(), tenant_id=tenant.id, user_id=owner.id, name="private job", message="original", cron_expr="0 9 * * *")
        db.add_all([tenant, inactive_ui, owner, attacker, job,
                    TenantMembership(user_id=owner.id, tenant_id=tenant.id),
                    TenantMembership(user_id=attacker.id, tenant_id=tenant.id),
                    CronJobRun(id=uuid4(), job_id=job.id, user_id=owner.id, tenant_id=tenant.id, status="completed", output="PRIVATE_OUTPUT")])
        await db.commit()
        app = FastAPI()
        app.include_router(router)
        app.state.scheduler = SimpleNamespace(run_now=AsyncMock(), update_job=lambda job: None, remove_job=lambda job_id: None)
        state = SimpleNamespace(db=db, tenant=tenant, owner=owner, attacker=attacker, job=job, actor=owner, app=app)
        async def db_override():
            yield db
        async def user_override():
            return state.actor
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_execution_user] = user_override
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            state.client = client
            yield state
    await engine.dispose()


@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None), ("PUT", "", {"message": "disclose private memories"}),
    ("DELETE", "", None), ("POST", "/run", None), ("GET", "/runs", None),
])
async def test_other_member_cannot_read_mutate_or_execute(env, method, suffix, body):
    env.actor = env.attacker
    response = await env.client.request(method, f"/api/cron-jobs/{env.job.id}{suffix}", json=body)
    assert response.status_code == 404, response.text
    assert env.job.message == "original"
    env.app.state.scheduler.run_now.assert_not_awaited()


async def test_lists_hide_other_members_configuration_and_output(env):
    env.actor = env.attacker
    for path in ("/api/cron-jobs", "/api/cron-jobs/runs"):
        response = await env.client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert response.json()["total"] == 0


async def test_owner_uses_actual_tenant_even_with_disabled_ui_selection(env):
    response = await env.client.put(f"/api/cron-jobs/{env.job.id}", json={"message": "authorized"})
    assert response.status_code == 200, response.text
    assert env.job.message == "authorized"
    response = await env.client.post(f"/api/cron-jobs/{env.job.id}/run")
    assert response.status_code == 200, response.text
    env.app.state.scheduler.run_now.assert_awaited_once_with(env.job.id)
    response = await env.client.get(f"/api/cron-jobs/{env.job.id}/runs")
    assert response.json()["items"][0]["output"] == "PRIVATE_OUTPUT"
    assert env.owner.tenant_id != env.tenant.id


@pytest.mark.parametrize("revocation", ["membership", "tenant", "user"])
async def test_object_operations_recheck_actual_membership(env, revocation):
    if revocation == "membership":
        await env.db.execute(delete(TenantMembership).where(TenantMembership.user_id == env.owner.id))
    elif revocation == "tenant":
        env.tenant.is_active = False
    else:
        env.owner.is_active = False
    await env.db.flush()
    with pytest.raises(ExecutionAccessError):
        await CronJobService.get_job(env.db, env.job.id, None, actor_user_id=env.owner.id)
    response = await env.client.post(f"/api/cron-jobs/{env.job.id}/run")
    assert response.status_code == 403
    env.app.state.scheduler.run_now.assert_not_awaited()


async def test_service_cannot_be_called_without_actor(env):
    with pytest.raises(TypeError, match="actor_user_id"):
        await CronJobService.update_job(env.db, env.job.id, env.tenant.id, message="unsafe")
    with pytest.raises(TypeError, match="actor_user_id"):
        await CronJobService.delete_job(env.db, env.job.id, env.tenant.id)
    assert await CronJobService.update_job(env.db, env.job.id, env.tenant.id,
                                          message="unsafe", actor_user_id=env.attacker.id) is None
    assert not await CronJobService.delete_job(env.db, env.job.id, env.tenant.id,
                                               actor_user_id=env.attacker.id)
