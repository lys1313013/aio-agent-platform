"""Discover usable models through the real dispatcher and create a cron job."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aio_agent_platform.core.execution_scope import bind_execution_scope, execution_tenant_id
from aio_agent_platform.cron_jobs.handlers import handle_create_cron_job
from aio_agent_platform.db.models import (
    Agent,
    CronJob,
    LLMModel,
    LLMProvider,
    Tenant,
    TenantMembership,
    User,
)
from aio_agent_platform.interface.routes.models import list_models
from aio_agent_platform.tools import model_catalog
from aio_agent_platform.tools.builtin import CREATE_CRON_JOB, register_builtin_tools
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import ToolRegistry


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setattr("aio_agent_platform.tools.executor.get_hook_manager", Mock())
    monkeypatch.setattr("aio_agent_platform.tools.executor.get_recorder", Mock())
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, None)
    executor.register_direct_handler("list_models", model_catalog.handle_list_models)
    executor.register_direct_handler("create_cron_job", handle_create_cron_job)
    return executor


async def query(executor, user_id, arguments=None):
    return await executor.execute("list_models", arguments or {}, "query", str(user_id), "session")


@pytest_asyncio.fixture
async def catalog(db_session, engine, monkeypatch):
    a, b = [Tenant(id=uuid4(), name=name, slug=str(uuid4())) for name in ("execution", "ui")]
    user = User(id=uuid4(), username="catalog-user", email="catalog@test.local",
                password_hash="test", tenant_id=b.id)
    agent = Agent(id=uuid4(), tenant_id=a.id, name="cron agent", created_by=user.id)
    providers = [LLMProvider(id=uuid4(), tenant_id=t.id, name=name, provider_type="openai",
                             api_key_encrypted="secret-must-not-appear", base_url="https://private.example")
                 for t, name in ((a, "available"), (a, "disabled"), (b, "foreign"))]
    providers[1].is_active = False
    models = []
    for name, tenant, provider in (
        ("default", a, providers[0]), ("vision", a, providers[0]),
        ("disabled-model", a, providers[0]), ("disabled-provider", a, providers[1]),
        ("foreign", b, providers[2]), ("foreign-provider", a, providers[2]),
    ):
        models.append(LLMModel(id=uuid4(), tenant_id=tenant.id, provider_id=provider.id,
                               name=name, model_name=f"upstream-{name}", is_default=name == "default",
                               is_multimodal=name == "vision", is_active=name != "disabled-model"))
    models.append(LLMModel(id=uuid4(), tenant_id=a.id, provider_id=uuid4(),
                           name="missing-provider", model_name="missing"))
    db_session.add_all([a, b, user, agent, *providers, *models,
                        TenantMembership(user_id=user.id, tenant_id=a.id),
                        TenantMembership(user_id=user.id, tenant_id=b.id)])
    await db_session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(model_catalog, "get_session_factory", lambda: factory)
    monkeypatch.setattr("aio_agent_platform.cron_jobs.handlers.get_session_factory", lambda: factory)
    monkeypatch.setattr("aio_agent_platform.cron_jobs.handlers.get_global_scheduler", lambda: None)
    return SimpleNamespace(a=a, b=b, user=user, agent=agent, models=models, factory=factory)


def test_tool_is_read_only_and_instructs_model_discovery(executor):
    tool = executor.registry.get("list_models")
    assert tool.permission_level == "read"
    assert tool.requires_sandbox is False
    assert tool.parameters["additionalProperties"] is False
    assert "list_models" in CREATE_CRON_JOB.parameters["properties"]["model_id"]["description"]


async def test_tool_requires_execution_context(executor):
    result = await query(executor, uuid4())
    assert not result.success
    assert "执行租户" in result.error


async def test_tool_cannot_choose_another_tenant(executor):
    user_id = uuid4()
    with bind_execution_scope(user_id, uuid4()):
        result = await query(executor, user_id, {"tenant_id": str(uuid4())})
    assert not result.success
    assert "Extra inputs" in result.error


@pytest.mark.postgres
async def test_discovery_uses_execution_tenant_and_only_exposes_metadata(catalog, executor, db_session):
    d = catalog
    with bind_execution_scope(d.user.id, d.a.id):
        result = await query(executor, d.user.id)
    assert result.success, result.error
    data = json.loads(result.output)
    assert data["tenant_id"] == str(d.a.id)
    assert [m["name"] for m in data["models"]] == ["default", "vision"]
    assert data["models"][0]["is_default"] is True
    assert data["models"][1]["is_multimodal"] is True
    assert data["models"][1]["model_name"] == "upstream-vision"
    assert data["models"][1]["provider"] == "available"
    assert set(data["models"][0]) == {"id", "name", "model_name", "provider", "is_default", "is_multimodal"}
    assert "secret-must-not-appear" not in result.output
    assert "private.example" not in result.output
    assert (await db_session.get(User, d.user.id)).tenant_id == d.b.id
    public_models = await list_models(SimpleNamespace(tenant_id=d.a.id), db_session)
    assert [str(m.id) for m in public_models] == [m["id"] for m in data["models"]]
    assert execution_tenant_id(d.user.id) is None


@pytest.mark.postgres
async def test_discovered_id_can_create_task(catalog, executor):
    d = catalog
    with bind_execution_scope(d.user.id, d.a.id):
        listed = await query(executor, d.user.id)
        model_id = json.loads(listed.output)["models"][1]["id"]
        created = await executor.execute("create_cron_job", {
            "name": "specified model", "cron_expr": "0 9 * * *", "message": "report",
            "agent_id": str(d.agent.id), "model_id": model_id,
        }, "create", str(d.user.id), "session")
    assert created.success, created.error
    assert "created successfully" in created.output
    async with d.factory() as db:
        job = await db.scalar(select(CronJob).where(CronJob.name == "specified model"))
        assert str(job.model_id) == model_id
        assert job.tenant_id == d.a.id


@pytest.mark.postgres
@pytest.mark.parametrize("revoked", ["membership", "user", "tenant"])
async def test_discovery_revalidates_access(catalog, executor, db_session, revoked):
    d = catalog
    if revoked == "membership":
        await db_session.execute(delete(TenantMembership).where(
            TenantMembership.user_id == d.user.id, TenantMembership.tenant_id == d.a.id))
    elif revoked == "user":
        d.user.is_active = False
    else:
        d.a.is_active = False
    await db_session.commit()
    with bind_execution_scope(d.user.id, d.a.id):
        result = await query(executor, d.user.id)
    assert not result.success
    assert "执行租户" in result.error
    assert not result.output


@pytest.mark.postgres
async def test_empty_catalog_and_concurrent_tenants(catalog, executor, db_session):
    d = catalog

    async def in_tenant(tenant):
        with bind_execution_scope(d.user.id, tenant.id):
            result = await query(executor, d.user.id)
            assert result.success, result.error
            return json.loads(result.output)

    a, b = await asyncio.gather(in_tenant(d.a), in_tenant(d.b))
    assert {m["name"] for m in a["models"]} == {"default", "vision"}
    assert {m["name"] for m in b["models"]} == {"foreign"}
    for model in d.models:
        model.is_active = False
    await db_session.commit()
    empty = await in_tenant(d.a)
    assert empty["models"] == []
    assert execution_tenant_id(d.user.id) is None
