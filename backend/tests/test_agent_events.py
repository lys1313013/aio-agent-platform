"""The internal event model preserves live, persisted and delegated output."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.core.agent_events import (
    ControlEvent,
    FileChangesEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    decode_agent_event,
    encode_agent_event,
)
from aio_agent_platform.core.chat_history import ChatTurnRecorder
from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.execution_scope import bind_execution_scope, execution_tenant_id
from aio_agent_platform.core.turn_metadata import InteractiveTurnMetadata
from aio_agent_platform.delegation.handler import _proxy_child_event
from aio_agent_platform.llm import LLMChunk, LLMStreamError
from aio_agent_platform.rooms.runtime import Output

EVENTS = [
    TextEvent("reasoning_delta", "检查：https://example.test/a:b\n下一行"),
    TextEvent("reasoning", "检查：https://example.test/a:b\n下一行"),
    ToolCallEvent("call-1", "read_file", {"path": "a:b.txt", "query": "中文\ntext"}),
    ToolResultEvent("call-1", "read_file", "ok", {"content": "a:b\n中文"}),
    FileChangesEvent([{"path": "a:b.txt", "action": "created"}]),
    TextEvent("text_delta", "结果：完成\n下一行"),
    ControlEvent("confirmation_flow", "ask-1", "waiting"),
    ControlEvent("ui_action", "ui-1", "heartbeat"),
    ControlEvent("delegation_heartbeat", status="drain"),
]


@pytest.mark.parametrize("event", EVENTS)
def test_wire_codec_roundtrips_structured_payloads_and_control_events(event):
    assert decode_agent_event(encode_agent_event(event)) == event


@pytest.mark.parametrize("legacy", [False, True])
async def test_chat_room_and_delegation_share_event_identity_and_content(legacy):
    turn = ChatTurnRecorder(uuid4(), uuid4())
    room = Output()
    queue = asyncio.Queue()
    delegation_id = uuid4()
    live = []
    for event in EVENTS:
        incoming = encode_agent_event(event) if legacy else event
        payload = turn.record(incoming)
        if payload:
            live.append(payload)
        room.consume(incoming)
        await _proxy_child_event(queue, delegation_id, incoming)

    assert turn.content == room.content == "结果：完成\n下一行"
    assert turn.reasoning[0]["content"] == room.payload["reasoning"][0]["content"]
    assert len(turn.reasoning) == len(room.payload["reasoning"]) == 1
    assert turn.tool_calls[0]["arguments"] == room.payload["tool_calls"][0]["arguments"]
    assert turn.tool_calls[0]["result"]["preview"] == {"content": "a:b\n中文"}
    assert turn.file_changes == room.payload["file_changes"]
    proxied = [queue.get_nowait() for _ in range(queue.qsize())]
    expected = [
        {**payload, "type": f"delegation_{payload['type']}", "delegation_id": str(delegation_id)}
        for payload in live if payload["type"] != "file_changes"
    ]
    assert proxied == expected


def test_room_file_changes_merge_without_importing_http_routes():
    room = Output()
    for action in ["created", "modified"]:
        assert room.consume(FileChangesEvent([{"path": "report.txt", "action": action}]))
    assert room.payload["file_changes"] == [{"path": "report.txt", "action": "created"}]
    room.consume('file_changes:[{"path": "report.txt", "action": "deleted"}]')
    assert room.payload["file_changes"] == []


@pytest.mark.parametrize("invalid", ["file_changes:{", "file_changes:{}", "file_changes:[null]"])
def test_invalid_legacy_file_payload_does_not_break_turn_recording(invalid):
    assert ChatTurnRecorder(uuid4(), uuid4()).record(invalid) is None
    assert not Output().consume(invalid)


def test_interactive_metadata_keeps_partial_results_and_merges_idempotently():
    metadata = InteractiveTurnMetadata()
    calls = [{"id": "parent", "name": "delegate_task"}, {"id": "ask", "name": "AskUserQuestion"}]
    metadata.track({"type": "delegation_start", "delegation_id": "d", "tool_call_id": "parent"})
    metadata.track({"type": "delegation_thinking", "delegation_id": "d", "content": "working"})
    metadata.track({"type": "delegation_tool_call", "delegation_id": "d", "id": "child", "name": "read_file"})
    metadata.track({"type": "delegation_tool_result", "delegation_id": "d", "tool_call_id": "child", "status": "ok", "preview": "found"})
    metadata.track({"type": "confirmation_required", "confirmation_id": "c", "question": "Continue?", "mode": "approve"})
    metadata.merge(calls)
    partial = deepcopy(calls)
    metadata.merge(calls)  # A cancellation rescue must not duplicate cards/results.
    assert calls == partial
    assert calls[0]["delegation"]["toolCalls"][0]["result"]["preview"] == "found"
    assert calls[1]["confirmation"]["question"] == "Continue?"
    metadata.track({"type": "confirmation_resolved", "confirmation_id": "c", "status": "approved"})
    metadata.track({"type": "delegation_end", "delegation_id": "d", "status": "completed", "result_preview": "done"})
    metadata.merge(calls)
    assert calls[0]["delegation"]["result"] == "done"
    assert calls[1]["confirmation"]["resolved"]["status"] == "approved"


async def test_disabled_provider_retry_is_respected_by_agent_loop(monkeypatch):
    provider = Mock(model="test", enable_retry=False)
    attempts = 0

    async def stream(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise LLMStreamError("disconnected before output")
        yield  # pragma: no cover

    provider.stream = stream
    monkeypatch.setattr("aio_agent_platform.core.agent.get_hook_manager", Mock(return_value=Mock()))
    monkeypatch.setattr("aio_agent_platform.core.agent.get_recorder", Mock(return_value=Mock()))
    loop = AgentLoop(provider, Mock(), execution_tenant_id=uuid4())
    loop.execution_agent_id = uuid4()
    with pytest.raises(LLMStreamError, match="before output"):
        async for _ in loop.run("hello", uuid4(), uuid4(), [], []):
            pass
    assert attempts == 1


async def test_closing_stream_closes_provider_in_its_own_execution_context(monkeypatch):
    user, tenant, agent, caller_tenant = [uuid4() for _ in range(4)]
    closed = []
    provider = Mock(model="test")

    async def stream(*args, **kwargs):
        owner = asyncio.current_task()
        try:
            yield LLMChunk(type="text_delta", content="partial")
            await asyncio.Event().wait()
        finally:
            closed.append((asyncio.current_task() is owner, execution_tenant_id(user), current_agent_id.get()))

    provider.stream = stream
    monkeypatch.setattr("aio_agent_platform.core.agent.get_hook_manager", Mock(return_value=Mock()))
    monkeypatch.setattr("aio_agent_platform.core.agent.get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr("aio_agent_platform.core.agent._resolve_agent_id", AsyncMock(return_value=agent))
    loop = AgentLoop(provider, Mock(), execution_tenant_id=tenant)
    with bind_execution_scope(user, caller_tenant):
        events = loop.run("hello", user, uuid4(), [], [])
        assert await asyncio.create_task(anext(events)) == "text_delta:partial"
        await asyncio.create_task(events.aclose())
        assert closed == [(True, tenant, str(agent))]
        assert execution_tenant_id(user) == caller_tenant
