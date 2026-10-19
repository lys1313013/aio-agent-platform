"""Cron execution must survive UI tenant switches without widening access."""

import asyncio
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aio_agent_platform.core import chat
from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.execution_scope import (
    bind_execution_scope,
    execution_tenant_id,
    resolve_execution_tenant,
)
from aio_agent_platform.cron_jobs.executor import execute_cron_job
from aio_agent_platform.cron_jobs.scheduler import Scheduler
from aio_agent_platform.db.models import (
    Agent,
    AgentKnowledgeBase,
    CronJob,
    CronJobRun,
    KnowledgeBase,
    Session,
    Tenant,
    TenantMembership,
    User,
    UserProfile,
)


@pytest_asyncio.fixture
async def cron_data(db_session, engine):
    tenant = Tenant(id=uuid4(), name="Job tenant", slug=str(uuid4()))
    ui_tenant = Tenant(id=uuid4(), name="UI tenant", slug=str(uuid4()))
    user = User(id=uuid4(), username="cron-owner", email="cron@example.com",
                password_hash="test", tenant_id=ui_tenant.id)
    agent = Agent(id=uuid4(), name="Private agent", tenant_id=tenant.id,
                  created_by=user.id, visibility="private", system_prompt="Run task")
    job = CronJob(id=uuid4(), name="Pinned task", user_id=user.id, tenant_id=tenant.id,
                  agent_id=agent.id, cron_expr="0 * * * *", message="check", is_active=True)
    db_session.add_all([
        tenant, ui_tenant, user, agent, job,
        TenantMembership(user_id=user.id, tenant_id=tenant.id),
        TenantMembership(user_id=user.id, tenant_id=ui_tenant.id),
        UserProfile(user_id=user.id, tenant_id=tenant.id, personal_portrait="JOB_PORTRAIT"),
        UserProfile(user_id=user.id, tenant_id=ui_tenant.id, personal_portrait="UI_PORTRAIT"),
    ])
    await db_session.commit()
    return SimpleNamespace(tenant=tenant, ui_tenant=ui_tenant, user=user, agent=agent,
                           job=job, factory=async_sessionmaker(engine, expire_on_commit=False))


@pytest.mark.postgres
@pytest.mark.parametrize("manual", [True, False])
async def test_cron_uses_job_tenant_after_ui_switch(cron_data, monkeypatch, manual):
    d = cron_data
    seen = []

    class Loop:
        provider = object()

        async def run(self, **kwargs):
            assert self.execution_tenant_id == d.tenant.id
            assert self.execution_agent_id == d.agent.id
            assert current_agent_id.get() == str(d.agent.id)
            async with d.factory() as db:
                assert await resolve_execution_tenant(db, d.user.id) == d.tenant.id
                # Memory writes/default-model resolution and cron tools share this resolver.
                from aio_agent_platform.cron_jobs.handlers import _get_tenant_id
                from aio_agent_platform.memory.service import resolve_tenant_id
                assert await _get_tenant_id(db, d.user.id) == d.tenant.id
                assert await resolve_tenant_id(db, d.user.id) == d.tenant.id
            seen.append(kwargs)
            yield "text_delta:done"

    async def build_loop(*args, **kwargs):
        assert kwargs["tenant_id"] == d.tenant.id
        assert "JOB_PORTRAIT" in args[1]
        assert "UI_PORTRAIT" not in args[1]
        return Loop()

    monkeypatch.setattr(chat, "build_agent_loop", build_loop)
    monkeypatch.setattr(chat, "refresh_mcp_tools_for_agent", AsyncMock())
    monkeypatch.setattr(chat, "filter_tools_by_agent", lambda *a, **kw: ([], []))
    monkeypatch.setattr("aio_agent_platform.core.context.prepare_context",
                        AsyncMock(return_value=([], None)))
    scheduler = Scheduler(d.factory, executor=partial(
        execute_cron_job, tool_executor=object(), push_to_channel=AsyncMock()))
    monkeypatch.setattr(scheduler._claims, "claim", AsyncMock(return_value=True))
    try:
        await (scheduler.run_now(d.job.id) if manual else scheduler._execute(d.job.id))
    finally:
        await scheduler._claims.close()
    assert len(seen) == 1
    assert execution_tenant_id(d.user.id) is None
    assert current_agent_id.get() is None
    async with d.factory() as db:
        run = await db.scalar(select(CronJobRun).where(CronJobRun.job_id == d.job.id))
        assert run.status == "success", run.error
        assert run.tenant_id == d.tenant.id
        assert run.output == "done"
        session = await db.get(Session, run.session_id)
        assert session.agent_id == d.agent.id
        assert session.source == "cron"
        assert (await db.get(User, d.user.id)).tenant_id == d.ui_tenant.id
        assert await resolve_execution_tenant(db, d.user.id) == d.ui_tenant.id


@pytest.mark.postgres
@pytest.mark.parametrize("denied", ["membership", "tenant", "user", "private", "agent_tenant", "agent"])
async def test_cron_rejects_revoked_or_cross_tenant_access(cron_data, db_session, monkeypatch, denied):
    d = cron_data
    if denied == "membership":
        await db_session.execute(delete(TenantMembership).where(
            TenantMembership.user_id == d.user.id, TenantMembership.tenant_id == d.tenant.id))
    elif denied == "tenant":
        d.tenant.is_active = False
    elif denied == "user":
        d.user.is_active = False
    elif denied == "private":
        d.agent.created_by = uuid4()
    elif denied == "agent_tenant":
        d.agent.tenant_id = d.ui_tenant.id
    else:
        d.agent.is_active = False
    await db_session.commit()
    build_loop = AsyncMock()
    monkeypatch.setattr(chat, "build_agent_loop", build_loop)
    scheduler = Scheduler(d.factory, executor=partial(
        execute_cron_job, tool_executor=object(), push_to_channel=AsyncMock()))
    try:
        await scheduler.run_now(d.job.id)
    finally:
        await scheduler._claims.close()
    build_loop.assert_not_awaited()
    assert execution_tenant_id(d.user.id) is None
    async with d.factory() as db:
        run = await db.scalar(select(CronJobRun).where(CronJobRun.job_id == d.job.id))
        assert run.status == "failed"
        assert run.error
        assert run.session_id is None
        assert (await db.get(User, d.user.id)).tenant_id == d.ui_tenant.id


@pytest.mark.postgres
async def test_explicit_tenant_preserves_knowledge_visibility(cron_data, db_session):
    d = cron_data
    allowed = KnowledgeBase(id=uuid4(), name="allowed", dataset_id="allowed",
                            tenant_id=d.tenant.id, created_by=d.user.id, visibility="private")
    hidden = KnowledgeBase(id=uuid4(), name="hidden", dataset_id="hidden",
                           tenant_id=d.tenant.id, created_by=uuid4(), visibility="private")
    foreign = KnowledgeBase(id=uuid4(), name="foreign", dataset_id="foreign",
                            tenant_id=d.ui_tenant.id, created_by=d.user.id, visibility="tenant")
    db_session.add_all([allowed, hidden, foreign])
    for kb in (allowed, hidden, foreign):
        db_session.add(AgentKnowledgeBase(agent_id=d.agent.id, knowledge_base_id=kb.id))
    await db_session.commit()
    async with d.factory() as db:
        user = await db.get(User, d.user.id)
        assert await chat.load_agent(db, d.agent.id, user=user) is None
        agent = await chat.load_agent(db, d.agent.id, user=user, tenant_id=d.tenant.id)
        assert [kb.id for kb in agent.knowledge_bases] == [allowed.id]
        assert user.tenant_id == d.ui_tenant.id
        assert not db.dirty


@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_scopes_isolate_concurrent_jobs_and_restore_on_error(error_type):
    user_id = uuid4()
    tenants = [uuid4(), uuid4()]
    agents = [uuid4(), uuid4()]
    entered = [asyncio.Event(), asyncio.Event()]

    async def run(index):
        with pytest.raises(error_type), bind_execution_scope(
            user_id, tenants[index], agent_id=agents[index]
        ):
            entered[index].set()
            await entered[1 - index].wait()
            assert execution_tenant_id(user_id) == tenants[index]
            assert current_agent_id.get() == str(agents[index])
            with pytest.raises(ValueError, match="用户不匹配"):
                execution_tenant_id(uuid4())
            raise error_type("cancel this job")
        assert execution_tenant_id(user_id) is None
        assert current_agent_id.get() is None

    await asyncio.gather(run(0), run(1))


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["knowledge", "graph"])
async def test_retrieval_tools_keep_job_tenant_and_visibility(cron_data, db_session, monkeypatch, kind):
    from aio_agent_platform.db.models import (
        AgentGraphKnowledgeBase,
        GraphKnowledgeBase,
        SystemConfig,
    )
    from aio_agent_platform.graph_knowledge import handlers as graph
    from aio_agent_platform.knowledge import handlers as knowledge

    d = cron_data
    model = KnowledgeBase if kind == "knowledge" else GraphKnowledgeBase
    binding = AgentKnowledgeBase if kind == "knowledge" else AgentGraphKnowledgeBase
    rows = []
    for name, tenant_id, creator in (
        ("allowed", d.tenant.id, d.user.id),
        ("private-other-owner", d.tenant.id, uuid4()),
        ("other-tenant", d.ui_tenant.id, d.user.id),
    ):
        values = {"id": uuid4(), "name": name, "tenant_id": tenant_id,
                  "created_by": creator, "visibility": "private"}
        if kind == "knowledge":
            values["dataset_id"] = name
        kb = model(**values)
        db_session.add_all([kb, binding(agent_id=d.agent.id, knowledge_base_id=kb.id)])
        rows.append(kb)
    db_session.add_all([SystemConfig(key="ragflow_base_url", value="https://rag.example"),
                        SystemConfig(key="ragflow_api_key", value="test-only")])
    await db_session.commit()
    module = knowledge if kind == "knowledge" else graph
    monkeypatch.setattr(module, "get_session_factory", lambda: d.factory)
    retrieve = AsyncMock(return_value=[] if kind == "knowledge" else {"entities": [], "relationships": []})
    monkeypatch.setattr(module, "retrieve" if kind == "knowledge" else "retrieve_subgraph", retrieve)
    handler = knowledge.handle_knowledge_retrieval if kind == "knowledge" else graph.handle_graph_retrieval
    with bind_execution_scope(d.user.id, d.tenant.id, agent_id=d.agent.id):
        await handler({"query": "test"}, str(d.user.id), str(uuid4()))
    retrieve.assert_awaited_once()
    if kind == "knowledge":
        assert retrieve.call_args.kwargs["dataset_ids"] == ["allowed"]
    else:
        assert retrieve.call_args.args[1] == rows[0].id


@pytest.mark.postgres
async def test_tool_writes_do_not_follow_ui_tenant(cron_data, monkeypatch):
    from aio_agent_platform.memory.service import MemoryService
    from aio_agent_platform.portrait import handlers

    d = cron_data
    monkeypatch.setattr(handlers, "get_session_factory", lambda: d.factory)
    with bind_execution_scope(d.user.id, d.tenant.id, agent_id=d.agent.id):
        await handlers.handle_update_user_portrait(
            {"portrait": "updated by cron"}, str(d.user.id), str(uuid4()))
        async with d.factory() as db:
            memory = await MemoryService.create_memory(db, d.user.id, "L2", "cron memory")
            assert memory.tenant_id == d.tenant.id
            await db.commit()
    async with d.factory() as db:
        job_profile = await db.get(UserProfile, (d.user.id, d.tenant.id))
        ui_profile = await db.get(UserProfile, (d.user.id, d.ui_tenant.id))
        assert job_profile.personal_portrait == "updated by cron"
        assert ui_profile.personal_portrait == "UI_PORTRAIT"
        assert (await db.get(User, d.user.id)).tenant_id == d.ui_tenant.id


@pytest.mark.postgres
@pytest.mark.parametrize("mode", ["default", "silent", "notify", "delivery_failed"])
async def test_cron_persists_report_and_delivery_separately(cron_data, db_session, monkeypatch, mode):
    from aio_agent_platform.channels.cron_notify import (
        current_cron_notify_ctx,
        handle_cron_respond,
    )

    d = cron_data
    d.job.channel_id = uuid4()
    await db_session.commit()
    seen = []

    class Loop:
        provider = object()

        async def run(self, **kwargs):
            if mode in ("silent", "notify"):
                result = await handle_cron_respond(
                    {"notify": mode == "notify", "summary": "完整检查摘要",
                     "reason": "信号未触发", "notification_text": "精简通知"},
                    str(d.user.id), str(kwargs["session_id"]),
                )
                assert not result.startswith("Error:")
            yield "text_delta:最终回答"

    async def push(db, job, text):
        assert execution_tenant_id(d.user.id) == d.tenant.id
        assert current_cron_notify_ctx.get() is None
        # A separate connection must see the completed report before sending.
        async with d.factory() as reader:
            run = await reader.scalar(select(CronJobRun).where(CronJobRun.job_id == job.id))
            assert run.status == "success"
            assert run.output == ("完整检查摘要" if mode == "notify" else "最终回答")
            assert run.delivery_status == "unknown"
        seen.append(text)
        return mode != "delivery_failed"

    monkeypatch.setattr(chat, "build_agent_loop", AsyncMock(return_value=Loop()))
    monkeypatch.setattr(chat, "refresh_mcp_tools_for_agent", AsyncMock())
    monkeypatch.setattr(chat, "filter_tools_by_agent", lambda *a, **kw: ([], []))
    monkeypatch.setattr("aio_agent_platform.core.context.prepare_context",
                        AsyncMock(return_value=([], None)))
    scheduler = Scheduler(d.factory, executor=partial(
        execute_cron_job, tool_executor=object(), push_to_channel=push))
    try:
        await scheduler.run_now(d.job.id)
    finally:
        await scheduler._claims.close()
    async with d.factory() as db:
        run = await db.scalar(select(CronJobRun).where(CronJobRun.job_id == d.job.id))
        assert run.status == "success", run.error
        if mode == "silent":
            assert seen == []
            assert run.output == "完整检查摘要"
            assert run.delivery_status == "suppressed"
            assert run.delivery_reason == "信号未触发"
        else:
            assert seen == ["精简通知" if mode == "notify" else "最终回答"]
            assert run.delivery_status == ("failed" if mode == "delivery_failed" else "sent")
        assert run.tenant_id == d.tenant.id
        assert (await db.get(User, d.user.id)).tenant_id == d.ui_tenant.id
    assert current_cron_notify_ctx.get() is None
    assert execution_tenant_id(d.user.id) is None
