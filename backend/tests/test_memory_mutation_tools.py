"""Memory references and explicit mutations, using only an isolated SQLite DB."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from aio_agent_platform.core import agent as agent_module
from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.db.models import Memory, MemoryChange, MemoryVersion, Session
from aio_agent_platform.llm import LLMChunk, ToolCall
from aio_agent_platform.memory import handlers, history
from aio_agent_platform.memory.presentation import format_memory, memory_reference
from aio_agent_platform.memory.service import MemoryService
from aio_agent_platform.tools.builtin import register_builtin_tools
from aio_agent_platform.tools.executor import ToolResult
from aio_agent_platform.tools.registry import ToolRegistry


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mutations.db'}")
    async with engine.begin() as connection:
        for model in (Memory, MemoryChange, MemoryVersion, Session):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(handlers, "get_session_factory", lambda: factory)
    monkeypatch.setattr(handlers, "_set_rls_context", AsyncMock())
    token = current_agent_id.set(None)
    yield factory
    current_agent_id.reset(token)
    await engine.dispose()


async def seed(factory, *, shared=False):
    user, agent = uuid4(), uuid4()
    async with factory() as db:
        session = Session(user_id=user, agent_id=agent)
        db.add(session)
        memory = await MemoryService.create_memory(
            db, user, "L1", "用户（山哥）是一名智能体平台开发者。",
            agent_id=None if shared else agent, meta={"tags": ["职业"]}, tenant_id=uuid4(),
        )
        await db.commit()
    return user, agent, session.id, memory


@pytest.mark.parametrize("shared", [False, True])
async def test_update_in_place_then_delete_and_undo(factory, shared):
    user, agent, session, memory = await seed(factory, shared=shared)
    read = await handlers.handle_memory_read({"query": "*"}, str(user), str(session))
    assert format_memory(memory) in read
    args = {"memory_id": str(memory.id), "expected_version": 1, "content": "常用语言 Java、Python"}
    updated = json.loads(await handlers.handle_memory_update(args, str(user), str(session)))
    assert updated["action"] == "updated"
    assert updated["memory"]["id"] == str(memory.id)
    assert updated["memory"]["version"] == 2
    assert updated["before"]["content"] == memory.content
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Memory)) == 1
        row = await db.get(Memory, memory.id)
        assert row.content == args["content"]
        assert row.layer == "L1"
        assert row.agent_id == (None if shared else agent)
        assert row.meta == memory.meta

    for mutation in (handlers.handle_memory_update, handlers.handle_memory_delete):
        with pytest.raises(ValueError, match="version conflict"):
            await mutation(args, str(user), str(session))

    args["expected_version"] = 2
    deleted = json.loads(await handlers.handle_memory_delete(args, str(user), str(session)))
    assert deleted["action"] == "deleted"
    async with factory() as db:
        assert await db.get(Memory, memory.id) is None
        change = await db.scalar(select(MemoryChange).where(MemoryChange.kind == "delete"))
        await history.undo(db, user, change.id)
        await db.commit()
        restored = await db.get(Memory, memory.id)
        assert restored.content == args["content"]
        assert restored.version == 4


@pytest.mark.parametrize("mutation", [handlers.handle_memory_update, handlers.handle_memory_delete])
@pytest.mark.parametrize("foreign_user", [False, True])
async def test_mutations_reject_other_agent_or_user(factory, mutation, foreign_user):
    user, agent, session, memory = await seed(factory)
    if foreign_user:
        user = uuid4()
    token = current_agent_id.set(str(agent if foreign_user else uuid4()))
    try:
        with pytest.raises(ValueError, match="not found or outside"):
            await mutation({"memory_id": str(memory.id), "expected_version": 1, "content": "bad"}, str(user), str(session))
    finally:
        current_agent_id.reset(token)
    async with factory() as db:
        assert (await db.get(Memory, memory.id)).content == memory.content
        assert await db.scalar(select(func.count()).select_from(MemoryChange)) == 1


async def test_child_context_takes_precedence_over_parent_session(factory):
    user, _agent, session, parent = await seed(factory)
    child = uuid4()
    async with factory() as db:
        memory = await MemoryService.create_memory(db, user, "L2", "child fact", agent_id=child, tenant_id=uuid4())
        await db.commit()
    token = current_agent_id.set(str(child))
    try:
        args = {"memory_id": str(memory.id), "expected_version": 1, "content": "child updated"}
        result = json.loads(await handlers.handle_memory_update(args, str(user), str(session)))
        assert result["memory"]["agent_id"] == str(child)
        args["memory_id"] = str(parent.id)
        with pytest.raises(ValueError, match="outside"):
            await handlers.handle_memory_update(args, str(user), str(session))
    finally:
        current_agent_id.reset(token)


@pytest.mark.parametrize("args", [
    {}, {"memory_id": "bad", "expected_version": 1},
    {"memory_id": str(uuid4()), "expected_version": True},
    {"memory_id": str(uuid4()), "expected_version": 0},
    {"memory_id": str(uuid4()), "expected_version": 1, "content": "  "},
])
async def test_invalid_mutation_does_not_open_database(monkeypatch, args):
    factory = Mock(side_effect=AssertionError("must validate first"))
    monkeypatch.setattr(handlers, "get_session_factory", factory)
    with pytest.raises(ValueError):
        await handlers.handle_memory_update(args, str(uuid4()), str(uuid4()))
    factory.assert_not_called()


@pytest.mark.parametrize("custom", [None, "Custom agent"])
def test_both_prompt_branches_include_references_and_policy(custom):
    shared = SimpleNamespace(id=uuid4(), version=3, layer="L1", agent_id=None, content="用户偏好\n中文")
    private = SimpleNamespace(id=uuid4(), version=4, layer="L2", agent_id=uuid4(), content="项目背景")
    prompt = build_system_prompt(agent_prompt=custom, persistent_memories=[shared], relevant_memories=[private])
    assert "- " + format_memory(shared) in prompt
    assert "- " + format_memory(private) in prompt
    assert "memory_update" in prompt and "memory_delete" in prompt


def test_mutation_tools_registered_with_required_version():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    for name in ("memory_update", "memory_delete"):
        tool = registry.get(name)
        assert {"memory_id", "expected_version"} <= set(tool.parameters["required"])
        assert name in handlers.MEMORY_HANDLERS


@pytest.mark.parametrize("delete,success", [(False, True), (True, True), (False, False)])
async def test_next_llm_request_receives_updated_system_memory(monkeypatch, delete, success):
    monkeypatch.setattr(agent_module, "_resolve_tenant_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "_resolve_agent_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr(agent_module, "get_hook_manager", Mock(return_value=Mock()))
    memory = SimpleNamespace(id=uuid4(), version=1, layer="L1", agent_id=None, content="OLD FACT")
    tool_name = "memory_delete" if delete else "memory_update"
    new = {**memory_reference(memory), "version": 2, "content": "NEW FACT"}
    output = json.dumps({"action": "deleted" if delete else "updated", "memory": new})
    requests = []

    async def stream(messages, tools=None):
        requests.append(deepcopy(messages))
        if len(requests) == 1:
            yield LLMChunk(type="tool_call_start", tool_call=ToolCall(id="edit", name=tool_name, arguments={}))
        else:
            yield LLMChunk(type="text_delta", content="完成")
        yield LLMChunk(type="done")

    executor = Mock()
    executor.execute = AsyncMock(return_value=ToolResult(
        tool_call_id="edit", name=tool_name, arguments={}, output=output, success=success, error="conflict",
    ))
    loop = agent_module.AgentLoop(execution_tenant_id=uuid4(), provider=Mock(model="test", stream=stream), tool_executor=executor,
                                  system_prompt=build_system_prompt(persistent_memories=[memory]))
    _events = [event async for event in loop.run(user_input="修改记忆", user_id=uuid4(), session_id=uuid4(), tools=[], conversation_history=[])]
    assert len(requests) == 2
    after = requests[1][0].content
    assert ("OLD FACT" in after) is not success
    assert ("NEW FACT" in after) is (success and not delete)
