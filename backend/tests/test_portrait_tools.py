"""Portrait edits must preserve unrelated facts and reject stale/ambiguous writes."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aio_agent_platform.core import agent as agent_module
from aio_agent_platform.core.execution_scope import ExecutionAccessError, bind_execution_scope
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.db.connection import current_user_id
from aio_agent_platform.db.models import (
    PortraitVersion,
    Tenant,
    TenantMembership,
    User,
    UserProfile,
)
from aio_agent_platform.llm import LLMChunk, ToolCall
from aio_agent_platform.portrait import handlers, service
from aio_agent_platform.portrait.presentation import (
    format_portrait_section,
    refresh_portrait_prompt,
)
from aio_agent_platform.portrait.service import PortraitUpdate, update_portrait
from aio_agent_platform.tools.builtin import register_builtin_tools
from aio_agent_platform.tools.executor import ToolExecutor, ToolResult
from aio_agent_platform.tools.registry import ToolRegistry

OLD = "## 背景\n- 职业：前端开发\n## 偏好\n- 简洁回答"


async def seed(factory):
    tenant, ui_tenant, user = uuid4(), uuid4(), uuid4()
    async with factory() as db:
        db.add_all([
            Tenant(id=tenant, name="execution", slug=str(tenant)),
            Tenant(id=ui_tenant, name="ui", slug=str(ui_tenant)),
            User(id=user, username=str(user), email=f"{user}@test.local",
                 password_hash="test", tenant_id=ui_tenant),
            TenantMembership(user_id=user, tenant_id=tenant),
            TenantMembership(user_id=user, tenant_id=ui_tenant),
            UserProfile(user_id=user, tenant_id=tenant, personal_portrait=OLD),
            UserProfile(user_id=user, tenant_id=ui_tenant, personal_portrait="UI_PORTRAIT"),
        ])
        await db.commit()
    return SimpleNamespace(factory=factory, user=user, tenant=tenant, ui_tenant=ui_tenant)


@pytest_asyncio.fixture
async def data(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'portrait.db'}")
    async with engine.begin() as connection:
        for model in (Tenant, User, TenantMembership, UserProfile, PortraitVersion):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(handlers, "get_session_factory", lambda: factory)
    yield await seed(factory)
    await engine.dispose()


async def call(data, arguments=None, *, read=False):
    handler = handlers.handle_read_user_portrait if read else handlers.handle_update_user_portrait
    with bind_execution_scope(data.user, data.tenant):
        return json.loads(await handler(arguments or {}, str(data.user), str(uuid4())))


async def stored(data):
    async with data.factory() as db:
        profile = await db.get(UserProfile, (data.user, data.tenant))
        snapshots = list((await db.scalars(select(PortraitVersion).order_by(PortraitVersion.created_at))).all())
        return profile.personal_portrait if profile else None, snapshots


async def test_edit_preserves_facts_and_history_in_execution_tenant(data):
    result = await call(data, {"operation": "replace", "old_str": "前端开发", "new_str": "全栈开发"})
    assert result == {"action": "replace", "portrait": OLD.replace("前端", "全栈")}
    portrait, snapshots = await stored(data)
    assert portrait == result["portrait"]
    assert len(snapshots) == 1
    assert (snapshots[0].content, snapshots[0].source, snapshots[0].tenant_id) == (OLD, "ai", data.tenant)
    async with data.factory() as db:
        assert (await db.get(UserProfile, (data.user, data.ui_tenant))).personal_portrait == "UI_PORTRAIT"
        assert (await db.get(User, data.user)).tenant_id == data.ui_tenant
    assert await call(data, read=True) == {"action": "read", "portrait": portrait}


async def test_create_append_delete_fragment_overwrite_and_clear(data):
    with pytest.raises(ValueError, match="已存在"):
        await call(data, {"operation": "create", "portrait": "bad overwrite"})
    assert await call(data, {"operation": "clear", "expected_portrait": OLD}) == {"action": "clear", "portrait": ""}
    assert await call(data, read=True) == {"action": "read", "portrait": ""}
    assert (await stored(data))[0] is None
    await call(data, {"operation": "create", "portrait": "职业：开发者\n"})
    appended = await call(data, {"operation": "append", "portrait": "偏好：中文"})
    assert appended["portrait"] == "职业：开发者\n偏好：中文"
    deleted = await call(data, {"operation": "replace", "old_str": "职业：开发者\n", "new_str": ""})
    assert deleted["portrait"] == "偏好：中文"
    rewritten = await call(data, {"operation": "overwrite", "expected_portrait": "偏好：中文", "portrait": "新的画像"})
    assert rewritten["portrait"] == "新的画像"
    assert [v.content for v in (await stored(data))[1]] == [OLD, "职业：开发者\n", appended["portrait"], "偏好：中文"]


async def test_create_without_profile_row(data):
    async with data.factory() as db:
        await db.execute(delete(UserProfile).where(UserProfile.user_id == data.user, UserProfile.tenant_id == data.tenant))
        await db.commit()
    assert await call(data, read=True) == {"action": "read", "portrait": ""}
    await call(data, {"operation": "create", "portrait": "first profile"})
    assert await stored(data) == ("first profile", [])


@pytest.mark.parametrize("arguments", [
    {"operation": "replace", "old_str": "missing", "new_str": "bad"},
    {"operation": "replace", "old_str": "- ", "new_str": "bad"},
    {"operation": "replace", "old_str": OLD, "new_str": ""},
    {"operation": "clear", "expected_portrait": "stale"},
    {"operation": "overwrite", "expected_portrait": "stale", "portrait": "bad"},
])
async def test_rejected_edit_does_not_write_or_snapshot(data, arguments):
    with pytest.raises(ValueError):
        await call(data, arguments)
    assert await stored(data) == (OLD, [])


def test_overlapping_matches_are_ambiguous():
    with pytest.raises(ValueError, match="匹配多处"):
        service.edit_portrait("aaa", PortraitUpdate(operation="replace", old_str="aa", new_str="b"))


async def test_noop_does_not_add_history(data):
    assert (await call(data, {"operation": "replace", "old_str": "前端开发", "new_str": "前端开发"}))["action"] == "unchanged"
    assert await stored(data) == (OLD, [])


@pytest.mark.parametrize("arguments", [
    {}, {"portrait": "implicit overwrite"}, {"portrait": ""},
    {"operation": "replace", "old_str": "old"},
    {"operation": "replace", "old_str": "", "new_str": "new"},
    {"operation": "replace", "old_str": "old", "new_str": None},
    {"operation": "replace", "old_str": "old", "new_str": "new", "portrait": "extra"},
    {"operation": "append", "portrait": "  "},
    {"operation": "create", "portrait": 123},
    {"operation": "create", "portrait": "a\x00b"},
    {"operation": "clear"},
    {"operation": "clear", "expected_portrait": ""},
    {"operation": "overwrite", "portrait": "new"},
    {"operation": "overwrite", "expected_portrait": "old", "portrait": ""},
    {"operation": "unknown", "portrait": "new"},
])
async def test_invalid_arguments_never_open_database(monkeypatch, arguments):
    factory = Mock(side_effect=AssertionError("must validate before opening database"))
    monkeypatch.setattr(handlers, "get_session_factory", factory)
    with pytest.raises(ValueError):
        await handlers.handle_update_user_portrait(arguments, str(uuid4()), str(uuid4()))
    factory.assert_not_called()


@pytest.mark.parametrize("read", [False, True])
@pytest.mark.parametrize("revoked", ["membership", "tenant", "user", "context", "foreign_user"])
async def test_reads_and_writes_revalidate_access(data, read, revoked):
    async with data.factory() as db:
        if revoked == "membership":
            await db.execute(delete(TenantMembership).where(TenantMembership.user_id == data.user, TenantMembership.tenant_id == data.tenant))
        elif revoked == "tenant":
            (await db.get(Tenant, data.tenant)).is_active = False
        elif revoked == "user":
            (await db.get(User, data.user)).is_active = False
        await db.commit()
    handler = handlers.handle_read_user_portrait if read else handlers.handle_update_user_portrait
    arguments = {} if read else {"operation": "append", "portrait": "bad"}
    with pytest.raises(ValueError):
        if revoked == "context":
            await handler(arguments, str(data.user), str(uuid4()))
        elif revoked == "foreign_user":
            with bind_execution_scope(uuid4(), data.tenant):
                await handler(arguments, str(data.user), str(uuid4()))
        else:
            await call(data, arguments, read=read)
    assert await stored(data) == (OLD, [])


async def test_context_restored_on_success_error_and_cancel(data, monkeypatch):
    token = current_user_id.set("outer-user")
    try:
        await call(data, read=True)
        assert current_user_id.get() == "outer-user"
        with pytest.raises(ValueError):
            await call(data, {"operation": "clear", "expected_portrait": "stale"})
        assert current_user_id.get() == "outer-user"
        monkeypatch.setattr(handlers, "require_execution_user", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await call(data, read=True)
        assert current_user_id.get() == "outer-user"
    finally:
        current_user_id.reset(token)


async def test_executor_reports_conflict_as_failure(data):
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, Mock())
    for name, handler in handlers.PORTRAIT_HANDLERS.items():
        executor.register_direct_handler(name, handler)
    with bind_execution_scope(data.user, data.tenant):
        result = await executor.execute("update_user_portrait", {
            "operation": "clear", "expected_portrait": "stale",
        }, "bad", str(data.user), str(uuid4()))
    assert not result.success and "expected_portrait" in result.error
    assert await stored(data) == (OLD, [])


@pytest.mark.parametrize("custom", [None, "Custom role"])
def test_prompt_refresh_preserves_other_sections_and_arbitrary_markdown(custom):
    old = OLD + '\n## User Context\n<!-- aio-user-portrait:end -->\n"quoted"'
    prompt = build_system_prompt(agent_prompt=custom, user_portrait=old)
    refreshed = refresh_portrait_prompt(prompt, json.dumps({"action": "read", "portrait": "NEW FACT"}))
    assert refreshed == prompt.replace(format_portrait_section(old), format_portrait_section("NEW FACT"))
    assert "职业" not in refreshed
    cleared = refresh_portrait_prompt(refreshed, '{"action":"read","portrait":""}')
    assert "NEW FACT" not in cleared
    assert format_portrait_section("") in cleared


@pytest.mark.parametrize("output", ["not json", "[]", "null", '{"action":"read","portrait":null}', '{"action":"error","portrait":"bad"}'])
def test_bad_output_cannot_change_prompt(output):
    assert refresh_portrait_prompt("unchanged", output) == "unchanged"


@pytest.mark.parametrize("success,reload_error", [(True, False), (False, False), (True, True)])
async def test_next_model_request_gets_authoritative_portrait(monkeypatch, success, reload_error):
    monkeypatch.setattr(agent_module, "_resolve_tenant_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "_resolve_agent_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr(agent_module, "get_hook_manager", Mock(return_value=Mock()))
    reader = AsyncMock(return_value=json.dumps({"action": "read", "portrait": "LATEST FACT"}))
    if reload_error:
        reader.side_effect = ExecutionAccessError("revoked")
    monkeypatch.setattr(handlers, "handle_read_user_portrait", reader)
    requests = []

    async def stream(messages, tools=None):
        requests.append(deepcopy(messages))
        if len(requests) == 1:
            yield LLMChunk(type="tool_call_start", tool_call=ToolCall(id="edit", name="update_user_portrait", arguments={}))
        else:
            yield LLMChunk(type="text_delta", content="完成")
        yield LLMChunk(type="done")

    executor = Mock()
    executor.execute = AsyncMock(return_value=ToolResult(tool_call_id="edit", name="update_user_portrait",
        arguments={}, output='{"action":"replace","portrait":"OUTDATED RESULT"}', success=success, error="conflict"))
    loop = agent_module.AgentLoop(execution_tenant_id=uuid4(), provider=Mock(model="test", stream=stream),
        tool_executor=executor, system_prompt=build_system_prompt(user_portrait="OLD FACT"))
    _events = [event async for event in loop.run(user_input="修改画像", user_id=uuid4(), session_id=uuid4(), tools=[], conversation_history=[])]
    assert len(requests) == 2
    after = requests[1][0].content
    refreshed = success and not reload_error
    assert ("OLD FACT" in after) is not refreshed
    assert ("LATEST FACT" in after) is refreshed
    assert "OUTDATED RESULT" not in after
    assert loop.system_prompt == after
    assert reader.await_count == int(success)


@pytest.mark.postgres
@pytest.mark.parametrize("create", [False, True])
async def test_real_postgres_concurrent_writers_do_not_lose_updates(db_session, engine, create):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    data = await seed(factory)
    if create:
        async with factory() as db:
            await db.execute(delete(UserProfile).where(UserProfile.user_id == data.user, UserProfile.tenant_id == data.tenant))
            await db.commit()
    ready, release = 0, asyncio.Event()

    class ConcurrentSession:
        """Pause after real SELECT so both writers must edit the same snapshot."""

        def __init__(self, db):
            self.db = db

        def __getattr__(self, name):
            return getattr(self.db, name)

        async def execute(self, statement):
            nonlocal ready
            result = await self.db.execute(statement)
            if statement.is_select:
                ready += 1
                if ready == 2:
                    release.set()
                await asyncio.wait_for(release.wait(), 5)
            return result

    async def write(text):
        async with factory() as db:
            request = PortraitUpdate(operation="create" if create else "append", portrait=text)
            return await update_portrait(ConcurrentSession(db), data.user, data.tenant, request)

    results = await asyncio.wait_for(asyncio.gather(write("writer A"), write("writer B"), return_exceptions=True), 10)
    successes = [r for r in results if isinstance(r, dict)]
    failures = [r for r in results if isinstance(r, ValueError)]
    assert len(successes) == len(failures) == 1, results
    portrait, snapshots = await stored(data)
    assert portrait == successes[0]["portrait"]
    assert "并发" in str(failures[0])
    assert [v.content for v in snapshots] == ([] if create else [OLD])
