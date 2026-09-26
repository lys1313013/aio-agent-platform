"""Durable runtime tests use an isolated SQLite DB, never application data."""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from aio_agent_platform.core import chat_runs as runtime
from aio_agent_platform.core.task_scope import call_key, track_task
from aio_agent_platform.db.models import ChatRun, ChatRunEvent, Message, Session


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.db'}")
    async with engine.begin() as conn:
        for model in (Session, Message, ChatRun, ChatRunEvent):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(runtime, "get_session_factory", lambda: factory)
    yield factory
    await runtime.shutdown()
    await engine.dispose()


async def reserve(factory, *, session=None, user=None, resumed_from=None):
    user = user or uuid4()
    async with factory() as db:
        if session is None:
            session = uuid4()
            db.add(Session(id=session, user_id=user, title="test"))
            await db.commit()
        run = await runtime.reserve(db, user, session, {"message": "test"}, resumed_from)
        db.add(Message(id=run.assistant_message_id, session_id=session, user_id=user, role="assistant", content=""))
        await db.commit()
    return run


async def current(factory, run):
    async with factory() as db:
        return await runtime.owned(db, run.user_id, run.id)


def events(chunks):
    return [json.loads(chunk[6:]) for chunk in chunks if chunk.startswith("data: ")]


async def test_disconnect_and_two_subscribers_do_not_restart_worker(factory):
    run = await reserve(factory)
    release = asyncio.Event()
    started = asyncio.Event()
    executions = 0

    async def source():
        nonlocal executions
        executions += 1
        yield runtime.sse({"type": "tool_call", "id": "t", "name": "write", "arguments": {}})
        yield runtime.sse({"type": "tool_result", "tool_call_id": "t", "status": "ok", "preview": "saved"})
        started.set()
        await release.wait()
        yield runtime.sse({"type": "done", "message_id": str(run.assistant_message_id), "content": "finished"})

    runtime.start(run, source())
    await asyncio.wait_for(started.wait(), 2)
    stream = runtime.subscribe(run.id, run.user_id)
    assert (await anext(stream)).startswith("data: ")
    await stream.aclose()
    assert not runtime._workers[run.id].done()
    release.set()
    one, two = await asyncio.gather(
        collect(runtime.subscribe(run.id, run.user_id)),
        collect(runtime.subscribe(run.id, run.user_id)),
    )
    assert executions == 1
    assert [e["type"] for e in events(one)] == [e["type"] for e in events(two)]
    assert events(one)[-1]["status"] == "completed"
    async with factory() as db:
        message = await db.get(Message, run.assistant_message_id)
        assert message.content == "finished"


async def collect(source):
    return [chunk async for chunk in source]


async def test_stop_cancels_children_and_preserves_unknown_tool(factory):
    run = await reserve(factory)
    ready = asyncio.Event()
    child_stopped = asyncio.Event()

    async def child():
        try:
            await asyncio.Event().wait()
        finally:
            child_stopped.set()

    async def source():
        yield runtime.sse({"type": "tool_call", "id": "x", "name": "submit_order", "arguments": {}})
        track_task(child())
        await asyncio.sleep(0)
        ready.set()
        await asyncio.Event().wait()
        yield runtime.sse({"type": "done", "content": "must not happen"})

    runtime.start(run, source())
    await asyncio.wait_for(ready.wait(), 2)
    async with factory() as db:
        result = await runtime.stop(db, run.user_id, run.id)
    assert result["status"] == "stopped"
    assert child_stopped.is_set()
    assert result["can_resume"] is False
    assert result["uncertain_tools"] == ["submit_order"]
    with pytest.raises(HTTPException, match="409"):
        await reserve(factory, session=run.session_id, user=run.user_id, resumed_from=run.id)


async def test_restart_expires_only_stale_run_and_restores_partial_answer(factory):
    stale = await reserve(factory)
    live = await reserve(factory)
    await runtime.append(stale.id, stale.user_id, [{"type": "text_delta", "content": "已完成分析"}])
    async with factory() as db:
        row = await runtime.owned(db, stale.user_id, stale.id)
        row.heartbeat_at = runtime.now() - timedelta(seconds=60)
        row.owner = "dead-process"
        await db.commit()
    async with factory() as db:
        restored = await runtime.latest(db, stale.user_id, stale.session_id)
        assert restored.status == "interrupted"
        assert runtime.describe(restored)["can_resume"]
        assert (await db.get(Message, stale.assistant_message_id)).content == "已完成分析"
    assert (await current(factory, live)).status == "running"
    with pytest.raises(asyncio.CancelledError):
        await runtime.append(stale.id, stale.user_id, [{"type": "text_delta", "content": "late"}])


async def test_duplicate_turn_is_rejected_and_resume_carries_completed_results(factory):
    first = await reserve(factory)
    with pytest.raises(HTTPException, match="409"):
        await reserve(factory, session=first.session_id, user=first.user_id)
    await runtime.append(first.id, first.user_id, [
        {"type": "tool_call", "id": "a", "name": "write", "arguments": {"path": "a"}},
        {"type": "tool_result", "tool_call_id": "a", "status": "ok", "preview": "written"},
    ])
    await runtime.finish(first.id, first.user_id, "interrupted")
    second = await reserve(factory, session=first.session_id, user=first.user_id, resumed_from=first.id)
    assert second.request["completed_calls"][call_key("write", {"path": "a"})]["preview"] == "written"
    await runtime.finish(second.id, second.user_id, "completed")
    with pytest.raises(HTTPException, match="409"):
        await reserve(factory, session=first.session_id, user=first.user_id, resumed_from=first.id)


async def test_events_and_mutations_are_owner_scoped(factory):
    run = await reserve(factory)
    other = uuid4()
    async with factory() as db:
        for operation in (runtime.owned(db, other, run.id), runtime.latest(db, other, run.session_id),
                          runtime.stop(db, other, run.id)):
            with pytest.raises(HTTPException, match="404"):
                await operation
    with pytest.raises(HTTPException, match="404"):
        await anext(runtime.subscribe(run.id, other))


async def test_cursor_replay_has_no_duplicates_and_marks_historical_actions(factory):
    run = await reserve(factory)
    await runtime.append(run.id, run.user_id, [
        {"type": "text_delta", "content": "a"},
        {"type": "ui_action_required", "action_id": "action"},
        {"type": "text_delta", "content": "b"},
    ])
    await runtime.finish(run.id, run.user_id, "interrupted")
    replay = events(await collect(runtime.subscribe(run.id, run.user_id, after=1)))
    assert [item["sequence"] for item in replay if "sequence" in item] == [2, 3]
    assert all(item["replay"] for item in replay if "sequence" in item)
    async with factory() as db:
        assert len((await db.scalars(select(ChatRunEvent))).all()) == 3


async def test_generation_error_marks_failed_and_keeps_partial_text(factory):
    run = await reserve(factory)

    async def source():
        yield runtime.sse({"type": "text_delta", "content": "partial"})
        raise RuntimeError("provider disconnected")

    runtime.start(run, source())
    task = runtime._workers[run.id]
    await task
    row = await current(factory, run)
    assert row.status == "failed"
    assert row.snapshot["content"] == "partial"
    assert runtime.describe(row)["can_resume"]


async def test_real_chat_route_disconnect_then_reconnect(factory, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from aio_agent_platform.core.agent import AgentStep
    from aio_agent_platform.db import connection
    from aio_agent_platform.db.models import LLMModel, LLMProvider
    from aio_agent_platform.interface.routes import chat
    from aio_agent_platform.llm import LLMMessage

    async with factory().bind.begin() as conn:
        for model in (LLMProvider, LLMModel):
            await conn.run_sync(model.__table__.create)
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    sid = uuid4()
    async with factory() as db:
        db.add(Session(id=sid, user_id=user.id, title="test"))
        await db.commit()
    ready, release = asyncio.Event(), asyncio.Event()
    calls = []

    class FakeLoop:
        provider = SimpleNamespace(model="test")
        max_iterations = 10

        async def run(self, **kwargs):
            calls.append(kwargs)
            yield 'tool_call:c:write_file:{"path":"report.md"}'
            yield 'tool_result:c:write_file:ok:"saved"'
            ready.set()
            await release.wait()
            yield "text_delta:done"
            yield AgentStep(step=1, done=True, final_output="done")

    monkeypatch.setattr(chat, "get_session_factory", lambda: factory)
    monkeypatch.setattr(connection, "get_session_factory", lambda: factory)
    monkeypatch.setattr(chat, "get_langfuse_client", lambda: None)
    for name, value in {
        "_load_agent": None, "generate_session_title": "", "load_pet_chat_context": None,
        "refresh_mcp_tools_for_agent": None, "_build_system_prompt_with_memories": "system",
        "_resolve_workspace": (None, None), "_build_agent_loop": FakeLoop(),
        "prepare_context": ([LLMMessage(role="user", content="write report")], None),
        "_update_context_summary": None,
    }.items():
        monkeypatch.setattr(chat, name, AsyncMock(return_value=value))
    monkeypatch.setattr(chat, "_filter_tools_by_agent", Mock(return_value=([], [])))
    monkeypatch.setattr(chat, "_fire_memory_extraction", Mock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tool_executor=Mock())))
    async with factory() as db:
        response = await chat.chat_stream(chat.ChatRequest(session_id=sid, message="write report"), request, user, db)
    await asyncio.wait_for(ready.wait(), 2)
    # Closing the actual response iterator must not propagate cancellation to AgentLoop.
    assert json.loads((await anext(response.body_iterator))[6:])["type"] == "run"
    await response.body_iterator.aclose()
    async with factory() as db:
        info = await chat.latest_chat_run(sid, user, db)
    assert info["status"] == "running"
    release.set()
    run_id = next(iter(runtime._workers))
    await runtime._workers[run_id]
    async with factory() as db:
        final = await chat.watch_chat_run(run_id, user, db, after=0)
    replay = events(await collect(final.body_iterator))
    assert len(calls) == 1
    assert next(e for e in replay if e["type"] == "done")["content"] == "done"
    async with factory() as db:
        messages = list((await db.scalars(select(Message).where(Message.session_id == sid))).all())
        assert [m.role for m in messages].count("user") == 1
        assistant = next(m for m in messages if m.role == "assistant")
        assert assistant.content == "done"
        assert assistant.tool_calls[0]["result"]["preview"] == "saved"


async def test_resume_reuses_completed_tool_instead_of_executing_again(monkeypatch):
    from unittest.mock import AsyncMock, Mock

    from aio_agent_platform.core import agent as agent_module
    from aio_agent_platform.core.task_scope import completed_calls
    from aio_agent_platform.llm import LLMChunk, ToolCall

    monkeypatch.setattr(agent_module, "_resolve_tenant_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "_resolve_agent_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr(agent_module, "get_hook_manager", Mock(return_value=Mock()))
    calls = []

    async def stream(messages, tools=None):
        calls.append(messages[:])
        if len(calls) == 1:
            yield LLMChunk(type="tool_call_start", tool_call=ToolCall(
                id="new-id", name="submit_order", arguments={},
            ), argument_delta='{"amount": 1}')
        else:
            yield LLMChunk(type="text_delta", content="continued")
        yield LLMChunk(type="done")

    executor = Mock(execute=AsyncMock())
    loop = agent_module.AgentLoop(provider=Mock(model="test", stream=stream), tool_executor=executor)
    token = completed_calls.set({call_key("submit_order", {"amount": 1}): {"status": "ok", "preview": "order 123"}})
    try:
        output = [e async for e in loop.run(user_input="continue", user_id=uuid4(), session_id=uuid4(),
                                           conversation_history=[], tools=[])]
    finally:
        completed_calls.reset(token)
    executor.execute.assert_not_called()
    assert 'tool_result:new-id:submit_order:ok:"order 123"' in output
    assert any(m.role == "tool" and m.content == "order 123" for m in calls[1])


async def test_shutdown_before_worker_first_step_persists_interruption(factory):
    run = await reserve(factory)

    async def source():
        raise AssertionError("shutdown must not start execution")
        yield ""  # pragma: no cover

    runtime.start(run, source())
    await runtime.shutdown()
    assert (await current(factory, run)).status == "interrupted"


async def test_completed_event_survives_crash_before_terminal_commit(factory):
    run = await reserve(factory)
    await runtime.append(run.id, run.user_id, [{"type": "done", "content": "complete"}])
    async with factory() as db:
        row = await runtime.owned(db, run.user_id, run.id)
        row.heartbeat_at = runtime.now() - timedelta(seconds=60)
        await db.commit()
    async with factory() as db:
        restored = await runtime.latest(db, run.user_id, run.session_id)
        assert restored.status == "completed"
        assert not runtime.describe(restored)["can_resume"]
