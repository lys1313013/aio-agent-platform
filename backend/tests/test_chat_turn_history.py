"""Live chat and background runs must persist the same replayable history."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.chat_history import ChatTurnRecorder
from aio_agent_platform.db.connection import current_user_id
from aio_agent_platform.db.models import Message
from aio_agent_platform.db.models import Session as ChatSession


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def history_factory():
    # Only a disposable in-memory message table; never touch the application DB.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(ChatSession.__table__.create)
        await connection.run_sync(Message.__table__.create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def saved_message(factory, turn):
    async with factory() as db:
        messages = (await db.scalars(select(Message).where(
            Message.session_id == turn.session_id,
        ))).all()
        assert len(messages) == 1
        assert messages[0].id == turn.message_id
        assert messages[0].user_id == turn.user_id
        return messages[0]


@pytest.mark.parametrize("live", [False, True], ids=["cron-and-rest", "sse-and-websocket"])
async def test_entry_paths_store_the_same_history(history_factory, live):
    turn = ChatTurnRecorder(uuid4(), uuid4())
    emitted = []
    file = {"path": "report.md", "action": "created", "size": 10}

    async def events():
        yield "reasoning_delta:检查"
        yield "reasoning:检查数据"
        yield 'tool_call:first:web_fetch:{"url":"https://example.com/path?a=1"}'
        yield 'tool_call:second:run_command:{"command":"echo ok"}'
        # Concurrent results can arrive out of call order.
        yield 'tool_result:second:run_command:err:"错误: failed"'
        yield 'tool_result:first:web_fetch:ok:"结果: ok\\n下一行"'
        # Read through a fresh DB session before the agent returns its answer.
        checkpoint = await saved_message(history_factory, turn)
        assert checkpoint.tool_calls[0]["result"]["status"] == "ok"
        yield "file_changes:" + json.dumps([file])
        yield "file_changes:" + json.dumps([{**file, "action": "modified", "size": 20}])
        yield "text_delta:检查"
        yield "text_delta:完成"
        yield AgentStep(step=2, done=True, final_output="检查完成")

    async with history_factory() as db:
        if live:
            async for event in events():
                payload = await turn.process(event, db)
                if payload:
                    emitted.append(payload)
            await turn.save(db)
        else:
            assert await turn.consume(events(), db) == "检查完成"

    message = await saved_message(history_factory, turn)
    assert message.content == "检查完成"  # No duplicated deltas/final answer.
    assert message.reasoning == [{"id": "thinking-0", "content": "检查数据"}]
    assert message.tool_calls == [
        {"id": "first", "name": "web_fetch",
         "arguments": {"url": "https://example.com/path?a=1"},
         "result": {"status": "ok", "preview": "结果: ok\n下一行"}},
        {"id": "second", "name": "run_command", "arguments": {"command": "echo ok"},
         "result": {"status": "err", "preview": "错误: failed"}},
    ]
    assert message.file_changes == [{**file, "size": 20}]
    if live:
        assert emitted[0] == {"type": "thinking", "content": "检查"}
        assert emitted[3] == {
            "type": "tool_result", "tool_call_id": "second", "name": "run_command",
            "status": "err", "preview": "错误: failed",
        }
        assert [event["content"] for event in emitted if event["type"] == "text_delta"] == [
            "检查", "完成",
        ]


@pytest.mark.parametrize("with_result", [False, True])
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_interruption_retains_partial_history(history_factory, with_result, error):
    turn = ChatTurnRecorder(uuid4(), uuid4())

    async def events():
        yield 'tool_call:call-1:web_fetch:{"url":"https://example.com"}'
        if with_result:
            yield 'tool_result:call-1:web_fetch:ok:"page content"'
        yield "text_delta:已检查"
        raise error("interrupted")

    async with history_factory() as db:
        with pytest.raises(error, match="interrupted"):
            await turn.consume(events(), db)

    message = await saved_message(history_factory, turn)
    assert message.content == "已检查"
    assert message.tool_calls[0]["id"] == "call-1"
    assert ("result" in message.tool_calls[0]) == with_result


async def test_rescue_updates_existing_row_and_keeps_interactive_metadata(history_factory, monkeypatch):
    monkeypatch.setattr("aio_agent_platform.db.connection.get_session_factory", lambda: history_factory)
    turn = ChatTurnRecorder(uuid4(), uuid4())
    async with history_factory() as db:
        await turn.process('tool_call:delegate:delegate_task:{"task":"check"}', db)
        await turn.process('tool_call:ask:AskUserQuestion:{"question":"continue?"}', db)
    message_id = turn.message_id
    # Metadata arrives on the SSE event queue after the latest checkpoint.
    turn.tool_calls[0]["delegation"] = {"status": "completed", "result": "done"}
    turn.tool_calls[1]["confirmation"] = {"resolved": {"status": "confirmed"}}
    turn.record("text_delta:partial answer")
    previous_user = current_user_id.get()
    await turn.rescue()
    assert current_user_id.get() == previous_user
    message = await saved_message(history_factory, turn)
    assert message.id == message_id
    assert message.content == "partial answer"
    assert message.tool_calls[0]["delegation"]["result"] == "done"
    assert message.tool_calls[1]["confirmation"]["resolved"]["status"] == "confirmed"


async def test_plain_answer_and_invalid_json(history_factory):
    turn = ChatTurnRecorder(uuid4(), uuid4())
    async with history_factory() as db:
        turn.record(AgentStep(step=1, done=True, final_output="完成"))
        await turn.save(db)
    message = await saved_message(history_factory, turn)
    assert message.content == "完成"
    assert message.tool_calls is None

    async with history_factory() as db:
        await turn.process("tool_call:bad:tool:invalid", db)
        await turn.process("tool_result:bad:tool:err:raw:error\x00", db)
        assert await turn.process("file_changes:invalid", db) is None
    message = await saved_message(history_factory, turn)
    assert message.tool_calls[0]["arguments"] == {}
    assert message.tool_calls[0]["result"] == {"status": "err", "preview": "raw:error"}


@pytest.mark.parametrize("channel_type", ["feishu", "wecom", "wecom_bot"])
@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
async def test_channel_pipeline_uses_shared_history(history_factory, monkeypatch, channel_type, outcome):
    from aio_agent_platform.channels import pipeline
    from aio_agent_platform.channels.adapter import InboundEvent
    from aio_agent_platform.channels.file_send import current_channel_send_ctx

    channel = SimpleNamespace(
        id=uuid4(), agent_id=uuid4(), tenant_id=uuid4(), channel_type=channel_type,
        tool_blacklist=[], enable_streaming=False,
    )
    agent = SimpleNamespace(
        model_id=None, temperature=None, max_iterations=5, enable_retry=True,
        tenant_id=channel.tenant_id, enable_auto_title=False, enable_memory_extraction=False,
    )
    adapter = SimpleNamespace(
        supports_file_send=False, max_message_bytes=3500,
        add_reaction=AsyncMock(return_value="typing"), delete_reaction=AsyncMock(),
        send_markdown=AsyncMock(return_value="reply"),
    )
    pipe = pipeline.ChannelInboundPipeline(channel, adapter, tool_executor=None)
    monkeypatch.setattr(pipe, "_resolve_vision_capability", AsyncMock(return_value=(False, "openai")))
    monkeypatch.setattr("aio_agent_platform.db.connection.get_session_factory", lambda: history_factory)
    monkeypatch.setattr(pipeline, "load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(pipeline, "refresh_mcp_tools_for_agent", AsyncMock())
    monkeypatch.setattr(pipeline, "filter_tools_by_agent", Mock(return_value=([], [])))
    monkeypatch.setattr(pipeline, "build_system_prompt_with_memories", AsyncMock(return_value="test"))
    monkeypatch.setattr(pipeline, "load_conversation_history", AsyncMock(return_value=([], None)))
    monkeypatch.setattr(pipeline, "resolve_workspace", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr(pipeline, "prepare_context", AsyncMock(return_value=([], False)))
    monkeypatch.setattr(pipeline, "fire_memory_extraction", Mock())
    monkeypatch.setattr(pipeline, "update_context_summary", AsyncMock())
    for name in ("task_started", "task_finished", "task_tool"):
        monkeypatch.setattr(pipeline, name, AsyncMock())
    logged = []

    async def log(_user_id, _session_id, event):
        logged.append(event)

    monkeypatch.setattr(pipeline, "log_event", log)
    file = {"path": "report.md", "action": "created", "size": 10}

    async def run(**_kwargs):
        yield "reasoning_delta:检查数据"
        yield "reasoning:检查数据"
        yield 'tool_call:call:run_command:{"command":"echo ok"}'
        yield 'tool_result:call:run_command:ok:"ok"'
        yield "file_changes:" + json.dumps([file])
        yield "text_delta:检查结果"
        if outcome == "failed":
            raise RuntimeError("provider failed")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        yield AgentStep(step=2, done=True, final_output="检查结果")

    monkeypatch.setattr(pipeline, "build_agent_loop", AsyncMock(
        return_value=SimpleNamespace(run=run, provider=Mock()),
    ))
    user_id, session_id = uuid4(), uuid4()
    event = InboundEvent(
        channel_id=channel.id, event_id="test", chat_id="chat", external_id="user", text="检查任务",
    )
    ctx = pipeline._ResolvedContext(user_id=user_id, session_id=session_id)
    previous_send_ctx = current_channel_send_ctx.get()
    async with history_factory() as db:
        db.add(ChatSession(id=session_id, user_id=user_id, source=channel_type))
        await db.commit()
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await pipe._drive_agent(db, event, ctx)
        else:
            await pipe._drive_agent(db, event, ctx)

    async with history_factory() as db:
        messages = (await db.scalars(select(Message).where(Message.session_id == session_id))).all()
    assert len(messages) == 2  # User + one assistant, including after rescue.
    assert next(message for message in messages if message.role == "user").content == "检查任务"
    assistant = next(message for message in messages if message.role == "assistant")
    assert assistant.content == ("检查结果\n\n⏹ 已中断" if outcome == "cancelled" else "检查结果")
    assert assistant.tool_calls[0]["result"] == {"status": "ok", "preview": "ok"}
    assert assistant.reasoning == [{"id": "thinking-0", "content": "检查数据"}]
    assert assistant.file_changes == [file]
    assert current_channel_send_ctx.get() is previous_send_ctx
    pipeline.task_finished.assert_awaited_once_with(user_id, session_id)
    adapter.delete_reaction.assert_awaited_once_with(event, "typing")
    assert any(item["type"] == "tool_result" and item["tool_call_id"] == "call" for item in logged)
    if outcome == "failed":
        assert logged[-1] == {"type": "error", "message": "provider failed"}
    else:
        assert logged[-1]["type"] == "done"
        assert logged[-1]["message_id"] == str(assistant.id)
        assert logged[-1]["reasoning"] == assistant.reasoning
        assert logged[-1]["file_changes"] == assistant.file_changes
        assert logged[-1].get("interrupted", False) == (outcome == "cancelled")
    adapter.send_markdown.assert_awaited()
    if pipeline.background_tasks:
        await asyncio.gather(*pipeline.background_tasks)
