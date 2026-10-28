"""The published API consumes the current AgentLoop event contract."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import anyio
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.execution_scope import execution_tenant_id
from aio_agent_platform.db.models import Message
from aio_agent_platform.db.models import Session as ChatSession
from aio_agent_platform.interface.routes import agent_api


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def external_chat(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(ChatSession.__table__.create)
        await conn.run_sync(Message.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    agent = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), skills=[], children=[], system_prompt="base")
    session_id = uuid4()
    async with factory() as db:
        db.add(ChatSession(id=session_id, user_id=user.id, agent_id=agent.id, source="api", context_summary="previous decision"))
        db.add(Message(id=uuid4(), session_id=session_id, user_id=user.id, role="user", content="repeat"))
        await db.commit()
    monkeypatch.setattr("aio_agent_platform.db.connection.get_session_factory", lambda: factory)
    monkeypatch.setattr(agent_api, "_load_agent_with_relations", AsyncMock(return_value=agent))
    monkeypatch.setattr(agent_api, "_get_active_version", AsyncMock(return_value=SimpleNamespace(config_snapshot={})))
    monkeypatch.setattr(agent_api, "_filter_tools_for_agent", Mock(return_value=([], [])))
    monkeypatch.setattr(agent_api, "build_system_prompt", Mock(return_value="base"))

    async def memory(*_args, **_kwargs):
        assert execution_tenant_id(user.id) == agent.tenant_id
        return {}

    monkeypatch.setattr(agent_api.MemoryService, "get_memories_for_prompt", memory)
    monkeypatch.setattr(agent_api.SkillService, "get_skills_for_prompt", AsyncMock(return_value=[]))
    monkeypatch.setattr("aio_agent_platform.workspaces.service.WorkspaceService.get_or_create_default", AsyncMock(return_value=SimpleNamespace(id=uuid4(), slug="default")))
    # Only the profile query uses a table absent from this disposable schema.
    real_execute = factory.class_.execute

    async def execute(db, statement, *args, **kwargs):
        if "user_profiles" in str(statement):
            return Mock(scalar_one_or_none=Mock(return_value=None))
        return await real_execute(db, statement, *args, **kwargs)

    monkeypatch.setattr(factory.class_, "execute", execute)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tool_executor=SimpleNamespace(mcp_manager=None))))
    yield SimpleNamespace(factory=factory, user=user, agent=agent, session_id=session_id, request=request)
    await engine.dispose()


@pytest.mark.parametrize("outcome", ["success", "plain", "final_only", "final_changed", "error", "disconnect", "cancel", "cancel_scope"])
async def test_stream_persists_current_events(external_chat, monkeypatch, outcome):
    fixture = external_chat
    observed = []
    closed = asyncio.Event()
    waiting = asyncio.Event()

    async def run(**kwargs):
        try:
            observed.extend(kwargs["conversation_history"])
            yield "reasoning_delta:checking"
            yield "reasoning:checking"
            if outcome not in {"plain", "final_only", "final_changed"}:
                yield 'tool_call:one:read_file:{"path":"a"}'
                yield 'tool_call:two:read_file:{"path":"b"}'
                yield 'tool_result:two:read_file:ok:"B"'
                yield 'tool_result:one:read_file:ok:"A"'
            if outcome != "final_only":
                yield "text_delta:answer"
            if outcome == "error":
                raise RuntimeError("provider unavailable")
            if outcome in {"disconnect", "cancel", "cancel_scope"}:
                waiting.set()
                await asyncio.Event().wait()
            yield AgentStep(step=2, done=True, final_output="iteration limit" if outcome == "final_changed" else "answer")
        finally:
            closed.set()

    monkeypatch.setattr(agent_api, "_build_agent_loop_for_version", AsyncMock(return_value=SimpleNamespace(provider=Mock(), run=run)))
    async with fixture.factory() as db:
        response = await agent_api.sse_chat(fixture.session_id, agent_api.SseChatRequest(message="repeat"), fixture.request, fixture.user, db)
    frames = []
    iterator = response.body_iterator
    async for frame in iterator:
        frames.append(frame)
        if outcome in {"disconnect", "cancel", "cancel_scope"} and "event: text_delta" in frame:
            if outcome == "disconnect":
                await asyncio.create_task(iterator.aclose())
            elif outcome == "cancel_scope":
                async def consume_rest():
                    async for remaining in iterator:
                        frames.append(remaining)
                async with anyio.create_task_group() as group:
                    group.start_soon(consume_rest)
                    await asyncio.wait_for(waiting.wait(), timeout=2)
                    group.cancel_scope.cancel()
            else:
                pending = asyncio.create_task(anext(iterator))
                await asyncio.wait_for(waiting.wait(), timeout=2)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            break
    assert closed.is_set()
    assert execution_tenant_id(fixture.user.id) is None
    assert any("previous decision" in str(m.content) for m in observed)
    assert sum(m.role == "user" and m.content == "repeat" for m in observed) == 1
    wire = "".join(frames)
    assert 'event: text_delta\ndata: {"text": "answer"}' in wire
    assert wire.count("event: tool_call\n") == (0 if outcome in {"plain", "final_only", "final_changed"} else 2)
    assert wire.count("event: tool_result\n") == (0 if outcome in {"plain", "final_only", "final_changed"} else 2)
    assert ("event: error" in wire) is (outcome == "error")
    assert ("event: done" in wire) is (outcome in {"success", "plain", "final_only", "final_changed"})
    async with fixture.factory() as db:
        rows = (await db.scalars(select(Message).where(Message.session_id == fixture.session_id).order_by(Message.created_at))).all()
    assert len(rows) == 3  # Old user turn, new user turn, one assistant row.
    assistant = next(m for m in rows if m.role == "assistant")
    assert assistant.content == ("iteration limit" if outcome == "final_changed" else "answer")
    if outcome in {"plain", "final_only", "final_changed"}:
        assert assistant.tool_calls is None
    else:
        assert [call["result"]["preview"] for call in assistant.tool_calls] == ["A", "B"]
    assert assistant.reasoning[0]["content"] == "checking"
    if outcome in {"success", "plain", "final_only", "final_changed"}:
        done = next(frame for frame in frames if frame.startswith("event: done"))
        payload = json.loads(done.split("data: ")[1])
        assert payload["message_id"] == str(assistant.id)
        assert payload["content"] == assistant.content


async def test_execute_rejects_session_bound_to_another_agent(external_chat):
    fixture = external_chat
    # The existing session belongs to another agent, although it has the same owner.
    async with fixture.factory() as db:
        session = await db.get(ChatSession, fixture.session_id)
        session.agent_id = uuid4()
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await agent_api._execute_agent_inner(
                fixture.agent.id, agent_api.ExecuteRequest(user_input="do not mix tenants", session_rid=str(fixture.session_id)),
                fixture.request, fixture.user, db,
            )
    assert error.value.status_code == 404


@pytest.mark.parametrize("transport", ["execute", "stream"])
@pytest.mark.parametrize("saved_prompt", ["missing", "", None, "published prompt"])
async def test_published_prompt_does_not_fall_back_when_explicitly_empty(
    external_chat, monkeypatch, transport, saved_prompt,
):
    fixture = external_chat
    snapshot = {} if saved_prompt == "missing" else {"system_prompt": saved_prompt}
    monkeypatch.setattr(agent_api, "_get_active_version", AsyncMock(
        return_value=SimpleNamespace(config_snapshot=snapshot),
    ))

    async def run(**_kwargs):
        yield AgentStep(step=1, done=True, final_output="answer")

    monkeypatch.setattr(agent_api, "_build_agent_loop_for_version", AsyncMock(
        return_value=SimpleNamespace(provider=Mock(), run=run),
    ))
    async with fixture.factory() as db:
        if transport == "execute":
            await agent_api._execute_agent_inner(
                fixture.agent.id,
                agent_api.ExecuteRequest(user_input="repeat", session_rid=str(fixture.session_id)),
                fixture.request, fixture.user, db,
            )
        else:
            response = await agent_api.sse_chat(
                fixture.session_id, agent_api.SseChatRequest(message="repeat"),
                fixture.request, fixture.user, db,
            )
            async for _frame in response.body_iterator:
                pass
    expected = fixture.agent.system_prompt if saved_prompt == "missing" else saved_prompt
    assert agent_api.build_system_prompt.call_args.kwargs["agent_prompt"] == expected
