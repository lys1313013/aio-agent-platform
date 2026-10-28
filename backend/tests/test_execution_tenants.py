"""Regression coverage for object-owned execution across tenant switches."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aio_agent_platform.core import agent as agent_module
from aio_agent_platform.core import chat
from aio_agent_platform.core.execution_scope import (
    ExecutionAccessError,
    bind_execution_scope,
    execution_tenant_id,
    require_execution_user,
    resolve_background_tenant,
)
from aio_agent_platform.db.models import (
    Agent,
    AgentRelationship,
    ChannelSessionMapping,
    ChatRoom,
    ChatRoomRun,
    CronJob,
    DailyMemory,
    LLMModel,
    LLMProvider,
    Session,
    Tenant,
    TenantMembership,
    User,
)
from aio_agent_platform.delegation import handler as delegation
from aio_agent_platform.memory.daily import DailyMemoryService, day_range_utc, local_today


@pytest_asyncio.fixture
async def scope_env(db_session, engine, monkeypatch):
    a = Tenant(id=uuid4(), name="Object tenant", slug=str(uuid4()))
    b = Tenant(id=uuid4(), name="UI tenant", slug=str(uuid4()))
    user = User(id=uuid4(), username="scope-user", email="scope@test.local",
                password_hash="test", tenant_id=b.id)
    parent = Agent(id=uuid4(), tenant_id=a.id, created_by=user.id, visibility="private", name="parent")
    child = Agent(id=uuid4(), tenant_id=a.id, created_by=user.id, visibility="private", name="child")
    session = Session(id=uuid4(), user_id=user.id, agent_id=parent.id)
    objects = [a, b, user, parent, child, session,
               TenantMembership(user_id=user.id, tenant_id=a.id),
               TenantMembership(user_id=user.id, tenant_id=b.id),
               AgentRelationship(parent_id=parent.id, child_id=child.id)]
    for t in (a, b):
        provider = LLMProvider(id=uuid4(), tenant_id=t.id, name=t.name, provider_type="openai")
        objects.extend([provider, LLMModel(id=uuid4(), tenant_id=t.id, provider_id=provider.id,
                       name=t.name, model_name=t.name, is_default=True)])
    db_session.add_all(objects)
    await db_session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("aio_agent_platform.db.connection.get_session_factory", lambda: factory)
    return SimpleNamespace(a=a, b=b, user=user, parent=parent, child=child,
                           session=session, factory=factory)


@pytest.mark.postgres
async def test_web_api_pet_and_session_use_object_tenant(scope_env, db_session):
    from aio_agent_platform.interface.routes.agent_api import _load_agent_with_relations
    from aio_agent_platform.pets.service import PetService

    d = scope_env
    for agent in (
        await chat.load_chat_agent(db_session, None, d.session.id, d.user),
        await _load_agent_with_relations(db_session, d.parent.id, d.user),
        await PetService(db_session).resolve_agent(d.user, SimpleNamespace(agent_id=d.parent.id),
                                                   SimpleNamespace(default_agent_id=None)),
    ):
        assert agent.id == d.parent.id
        assert agent.tenant_id == d.a.id
    assert d.user.tenant_id == d.b.id
    assert not db_session.dirty


@pytest.mark.postgres
@pytest.mark.parametrize("denied", ["membership", "user", "tenant", "private", "agent"])
async def test_object_entry_points_do_not_bypass_access(scope_env, db_session, denied):
    d = scope_env
    if denied == "membership":
        await db_session.execute(delete(TenantMembership).where(
            TenantMembership.user_id == d.user.id, TenantMembership.tenant_id == d.a.id))
    elif denied == "user":
        d.user.is_active = False
    elif denied == "tenant":
        d.a.is_active = False
    elif denied == "private":
        d.parent.created_by = uuid4()
    else:
        d.parent.is_active = False
    await db_session.commit()
    assert await chat.load_execution_agent(db_session, d.parent.id, d.user) is None
    with pytest.raises(ExecutionAccessError):
        await resolve_background_tenant(db_session, d.user.id, session_id=d.session.id)
    assert d.user.tenant_id == d.b.id


@pytest.mark.postgres
async def test_existing_and_dynamic_delegation_inherit_tenant(scope_env, db_session, monkeypatch):
    d = scope_env
    with bind_execution_scope(d.user.id, d.a.id):
        child = await delegation._load_child_agent(db_session, d.child.id, d.parent.id, d.user.id)
        assert child.id == d.child.id
        dynamic = delegation._build_dynamic_agent(uuid4(), "specialist", "task", d.a.id)
        create = Mock(return_value=object())
        monkeypatch.setattr(chat, "create_provider", create)
        await delegation._build_child_provider(db_session, dynamic)
        assert create.call_args.kwargs["model"] == d.a.name
        assert dynamic.tenant_id == d.a.id
    d.child.tenant_id = d.b.id
    await db_session.commit()
    with bind_execution_scope(d.user.id, d.a.id):
        assert await delegation._load_child_agent(db_session, d.child.id, d.parent.id, d.user.id) is None


@pytest.mark.postgres
async def test_channel_commands_use_channel_tenant(scope_env, db_session):
    from aio_agent_platform.channels.pipeline import ChannelInboundPipeline, _ResolvedContext

    d = scope_env
    db_session.add_all([CronJob(id=uuid4(), tenant_id=t.id, user_id=d.user.id,
                               agent_id=d.parent.id, name=name, cron_expr="0 9 * * *")
                       for t, name in [(d.a, "CHANNEL_JOB"), (d.b, "UI_JOB")]])
    await db_session.commit()
    channel = SimpleNamespace(id=uuid4(), tenant_id=d.a.id, channel_type="feishu")
    adapter = SimpleNamespace(send=AsyncMock())
    pipeline = ChannelInboundPipeline(channel=channel, adapter=adapter, tool_executor=Mock())
    event = SimpleNamespace(chat_id="chat", external_id="user", text="/cron list")
    with bind_execution_scope(d.user.id, d.a.id):
        await require_execution_user(db_session, d.user.id, channel.tenant_id)
        assert await pipeline._handle_unified_command(
            db_session, event, _ResolvedContext(d.user.id, d.session.id), event.text)
    reply = adapter.send.call_args.args[1]
    assert "CHANNEL_JOB" in reply and "UI_JOB" not in reply
    await db_session.refresh(d.user)
    assert d.user.tenant_id == d.b.id


@pytest.mark.postgres
async def test_channel_switch_cannot_rebind_to_another_tenant(scope_env, db_session):
    from aio_agent_platform.channels.pipeline import ChannelInboundPipeline, _ResolvedContext

    d = scope_env
    foreign = Agent(id=uuid4(), tenant_id=d.b.id, created_by=d.user.id, name="other")
    target = Session(id=uuid4(), user_id=d.user.id, agent_id=foreign.id)
    channel = SimpleNamespace(id=uuid4(), tenant_id=d.a.id, channel_type="feishu")
    mapping = ChannelSessionMapping(channel_id=channel.id, chat_id="chat", external_id="user",
                                    session_id=d.session.id)
    db_session.add_all([foreign, target, mapping])
    await db_session.commit()
    adapter = SimpleNamespace(send=AsyncMock())
    pipeline = ChannelInboundPipeline(channel=channel, adapter=adapter, tool_executor=Mock())
    event = SimpleNamespace(chat_id="chat", external_id="user")
    await pipeline._handle_unified_command(
        db_session, event, _ResolvedContext(d.user.id, d.session.id), f"/switch {target.id}")
    await db_session.refresh(mapping)
    assert mapping.session_id == d.session.id
    assert "无法切换" in adapter.send.call_args.args[1]
    target.agent_id = d.parent.id
    await db_session.commit()
    await pipeline._handle_unified_command(
        db_session, event, _ResolvedContext(d.user.id, d.session.id), f"/switch {target.id}")
    await db_session.refresh(mapping)
    assert mapping.session_id == target.id


@pytest.mark.postgres
async def test_room_monitor_survives_ui_switch_but_stops_on_revocation(scope_env, db_session, monkeypatch):
    from aio_agent_platform.rooms import runtime

    d = scope_env
    room = ChatRoom(id=uuid4(), tenant_id=d.a.id, user_id=d.user.id, workspace_id=uuid4(),
                    title="room", goal="goal", default_member_id=uuid4())
    run = ChatRoomRun(id=uuid4(), room_id=room.id, request_id=uuid4(), request_hash="test", status="running")
    db_session.add_all([room, run])
    await db_session.commit()
    monkeypatch.setattr(runtime, "get_session_factory", lambda: d.factory)
    # Advance the heartbeat on each iteration without waiting five real seconds.
    ticks = iter(range(10, 1000, 10))
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    task = asyncio.create_task(runtime._monitor(room.id, run.id))
    try:
        await asyncio.sleep(0.6)
        assert not task.done()
        async with d.factory() as db:
            assert (await db.get(ChatRoomRun, run.id)).heartbeat_at is not None
            await db.execute(delete(TenantMembership).where(
                TenantMembership.user_id == d.user.id, TenantMembership.tenant_id == d.a.id))
            await db.commit()
        assert await asyncio.wait_for(task, timeout=2) == "interrupted"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.postgres
async def test_delayed_memory_uses_source_tenant_for_model_and_write(scope_env, db_session, monkeypatch):
    from aio_agent_platform import llm
    from aio_agent_platform.memory.service import MemoryService

    d = scope_env
    complete = AsyncMock(return_value=SimpleNamespace(content='{"l3_summary":"source summary"}'))
    create = Mock(return_value=SimpleNamespace(complete=complete))
    monkeypatch.setattr(llm, "create_provider", create)
    result = await MemoryService.extract_memories_from_conversation(
        d.user.id, d.session.id, [{"role": "user", "content": "hello"}], tenant_id=d.a.id)
    assert result and all(m.tenant_id == d.a.id for m in result)
    assert create.call_args.kwargs["model"] == d.a.name
    assert execution_tenant_id(d.user.id) is None
    assert (await db_session.get(User, d.user.id)).tenant_id == d.b.id


@pytest.mark.postgres
async def test_daily_batch_uses_agent_tenant_and_skips_ambiguous_history(scope_env, db_session, monkeypatch):
    from aio_agent_platform import llm

    d = scope_env
    day = local_today() - timedelta(days=1)
    start, _ = day_range_utc(day)
    d.session.updated_at = start
    await db_session.commit()
    create = Mock(return_value=SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(
        content='{"content":"daily result","highlights":[]}'
    ))))
    monkeypatch.setattr(llm, "create_provider", create)
    result = await DailyMemoryService.consolidate_day(d.user.id, day, agent_id=d.parent.id)
    assert result is not None and result.tenant_id == d.a.id
    assert create.call_args.kwargs["model"] == d.a.name
    create.reset_mock()
    assert await DailyMemoryService.consolidate_day(d.user.id, day) is None
    create.assert_not_called()
    assert len((await db_session.scalars(select(DailyMemory))).all()) == 1


@pytest.mark.postgres
async def test_background_rejects_mismatched_snapshot(scope_env, db_session):
    d = scope_env
    with pytest.raises(ExecutionAccessError):
        await resolve_background_tenant(db_session, d.user.id, session_id=d.session.id, tenant_id=d.b.id)
    with pytest.raises(ExecutionAccessError):
        await resolve_background_tenant(db_session, d.user.id, session_id=uuid4(), tenant_id=d.a.id)
    with pytest.raises(ExecutionAccessError):
        await resolve_background_tenant(db_session, d.user.id, session_id=d.session.id,
                                        agent_id=d.child.id, tenant_id=d.a.id)


def test_nested_scope_clears_agent_and_restores_parent():
    from aio_agent_platform.core.context import current_agent_id

    user_id, tenant_id, agent_id = uuid4(), uuid4(), uuid4()
    with bind_execution_scope(user_id, tenant_id, agent_id=agent_id):
        with bind_execution_scope(user_id, tenant_id):
            assert current_agent_id.get() is None
        assert current_agent_id.get() == str(agent_id)
    assert current_agent_id.get() is None


async def test_loop_requires_scope_and_cleans_it_on_cancel(monkeypatch):
    from aio_agent_platform.llm import LLMChunk
    from aio_agent_platform.observation.recorder import get_obs_context

    user_id, tenant_id, agent_id = uuid4(), uuid4(), uuid4()
    started = asyncio.Event()

    async def stream(*args, **kwargs):
        assert execution_tenant_id(user_id) == tenant_id
        assert get_obs_context().tenant_id == tenant_id
        started.set()
        await asyncio.Event().wait()
        yield LLMChunk(type="done")

    monkeypatch.setattr(agent_module, "get_hook_manager", lambda: Mock())
    monkeypatch.setattr(agent_module, "get_recorder", lambda: Mock())
    loop = agent_module.AgentLoop(provider=Mock(model="test", stream=stream), tool_executor=Mock())
    with pytest.raises(ValueError, match="explicit execution tenant"):
        await anext(loop.run("test", user_id, uuid4(), [], []))
    loop.execution_tenant_id = tenant_id
    loop.execution_agent_id = agent_id

    async def consume():
        try:
            async for _ in loop.run("test", user_id, uuid4(), [], []):
                pass
        finally:
            assert execution_tenant_id(user_id) is None

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert execution_tenant_id(user_id) is None


@pytest.mark.postgres
async def test_identity_auth_ignores_disabled_ui_tenant_but_management_does_not(scope_env, db_session):
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from aio_agent_platform.auth.dependencies import get_current_user, get_execution_user
    from aio_agent_platform.auth.jwt_handler import create_access_token

    d = scope_env
    d.b.is_active = False
    await db_session.commit()
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=create_access_token(d.user.id, d.user.role))
    user = await get_execution_user(credentials, db_session)
    assert (await chat.load_chat_agent(db_session, None, d.session.id, user)).tenant_id == d.a.id
    with pytest.raises(HTTPException):
        await get_current_user(user, db_session)
    d.user.is_active = False
    await db_session.commit()
    with pytest.raises(HTTPException):
        await get_execution_user(credentials, db_session)


@pytest.mark.postgres
async def test_room_access_uses_room_tenant_and_rechecks_membership(scope_env, db_session):
    from fastapi import HTTPException

    from aio_agent_platform.rooms.service import owned_room
    d = scope_env
    room = ChatRoom(id=uuid4(), tenant_id=d.a.id, user_id=d.user.id, workspace_id=uuid4(),
                    title="room", goal="goal", default_member_id=uuid4())
    db_session.add(room)
    d.b.is_active = False
    await db_session.commit()
    assert (await owned_room(db_session, room.id, d.user)).id == room.id
    await db_session.execute(delete(TenantMembership).where(
        TenantMembership.user_id == d.user.id, TenantMembership.tenant_id == d.a.id))
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await owned_room(db_session, room.id, d.user)
    assert exc.value.status_code == 403


@pytest.mark.postgres
async def test_portrait_and_compact_use_command_execution_tenant(scope_env, db_session, monkeypatch):
    from aio_agent_platform.db.models import UserProfile
    from aio_agent_platform.interface.commands.handlers import context
    from aio_agent_platform.interface.commands.handlers.skills_memory import cmd_portrait
    from aio_agent_platform.interface.commands.models import CommandContext
    from aio_agent_platform.llm import LLMMessage
    d = scope_env
    db_session.add_all([UserProfile(user_id=d.user.id, tenant_id=t.id, personal_portrait=t.name)
                        for t in (d.a, d.b)])
    await db_session.flush()
    ctx = CommandContext(user=d.user, user_id=str(d.user.id), db=db_session, raw="/portrait",
                         session_id=str(d.session.id), session=d.session, execution_tenant_id=d.a.id)
    assert (await cmd_portrait(ctx)).content == d.a.name
    monkeypatch.setattr(context, "_history_messages", AsyncMock(return_value=([LLMMessage(role="user", content="hello")], None)))
    create = Mock(return_value=Mock())
    monkeypatch.setattr(context, "create_provider", create)
    monkeypatch.setattr(context, "generate_summary", AsyncMock(return_value="summary"))
    await context.cmd_compact(ctx)
    assert create.call_args.kwargs["model"] == d.a.name


@pytest.mark.postgres
async def test_cron_creation_validates_scope_and_deduplicates(scope_env, db_session):
    from aio_agent_platform.cron_jobs.service import CronJobConflict, CronJobService
    d = scope_env
    kwargs = {"db": db_session, "tenant_id": d.a.id, "user_id": d.user.id, "agent_id": d.parent.id,
              "name": "weekly", "task_config": {}, "cron_expr": "0 18 * * 0", "request_id": uuid4()}
    job = await CronJobService.create_job(**kwargs)
    assert job.tenant_id == d.a.id and job.cron_expr == "0 18 * * sun"
    assert (await CronJobService.create_job(**kwargs)).id == job.id
    with pytest.raises(CronJobConflict):
        await CronJobService.create_job(**{**kwargs, "message":"changed"})
    with pytest.raises(CronJobConflict):
        await CronJobService.create_job(**{**kwargs, "request_id":uuid4()})
    with pytest.raises(CronJobConflict):
        await CronJobService.create_job(**{**kwargs, "request_id":None, "message":"changed"})
    duplicate = await CronJobService.create_job(**{**kwargs, "request_id":None, "message":"changed", "allow_duplicate":True})
    assert duplicate.id != job.id
    for overrides in ({"tenant_id":d.b.id}, {"channel_id":uuid4()}):
        with pytest.raises(ExecutionAccessError):
            await CronJobService.create_job(**{**kwargs, **overrides})
    job.cron_expr = "0 18 * * 0"
    await db_session.flush()
    await CronJobService.update_job(db_session, job.id, d.a.id, actor_user_id=d.user.id, cron_expr=job.cron_expr, name="renamed")
    assert job.cron_expr == "0 18 * * mon"
    foreign = Agent(id=uuid4(), tenant_id=d.b.id, created_by=d.user.id, name="foreign")
    db_session.add(foreign)
    await db_session.flush()
    with pytest.raises(ExecutionAccessError):
        await CronJobService.update_job(db_session, job.id, d.a.id, actor_user_id=d.user.id, agent_id=foreign.id)


@pytest.mark.postgres
async def test_room_events_survive_ui_switch_and_end_when_access_revoked(scope_env, db_session, monkeypatch):
    from aio_agent_platform.interface.routes import rooms
    d = scope_env
    room = ChatRoom(id=uuid4(), tenant_id=d.a.id, user_id=d.user.id, workspace_id=uuid4(),
                    title="room", goal="goal", default_member_id=uuid4())
    db_session.add(room)
    d.b.is_active = False
    await db_session.commit()
    monkeypatch.setattr(rooms, "get_session_factory", lambda: d.factory)
    # Snapshot formatting is independently covered by room API tests.
    monkeypatch.setattr(rooms.service, "room_detail", AsyncMock(return_value={"id":str(room.id)}))
    response = await rooms.events(room.id, SimpleNamespace(is_disconnected=AsyncMock(return_value=False)),
                                  d.user, db_session, after_revision=-1)
    stream = response.body_iterator
    try:
        assert '"type": "snapshot"' in await anext(stream)
        await db_session.execute(delete(TenantMembership).where(
            TenantMembership.user_id == d.user.id, TenantMembership.tenant_id == d.a.id))
        await db_session.commit()
        assert 'access_revoked' in await anext(stream)
    finally:
        await stream.aclose()


@pytest.mark.postgres
async def test_concurrent_cron_retries_create_one_row(scope_env, db_session):
    from aio_agent_platform.cron_jobs.service import CronJobService
    d = scope_env
    request_id = uuid4()
    async def create():
        async with d.factory() as db:
            job = await CronJobService.create_job(db, d.a.id, d.user.id, "retry", {},
                agent_id=d.parent.id, cron_expr="30 14 * * 1-5", request_id=request_id)
            await db.commit()
            return job.id
    ids = await asyncio.gather(create(), create())
    assert ids[0] == ids[1]
    jobs = (await db_session.scalars(select(CronJob).where(CronJob.name == "retry"))).all()
    assert len(jobs) == 1
