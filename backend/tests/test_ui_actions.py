"""Tests for the frontend UI action protocol (docs/22-浏览器页面自动化).

Covers UiActionManager lifecycle (create/resolve/timeout/cancel/breaker) and
the AgentLoop interception branch (_run_ui_action_flow fast-fail paths).
"""

import asyncio
import uuid
from unittest.mock import MagicMock

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.core.ui_action import (
    REF_FAILURE_BREAKER_THRESHOLD,
    UiActionManager,
)
from aio_agent_platform.llm import LLMChunk, ToolCall
from aio_agent_platform.tools.registry import Tool, ToolRegistry


def _make_manager() -> UiActionManager:
    return UiActionManager()


# ---- UiActionManager ----


async def test_create_and_resolve():
    m = _make_manager()
    p = m.create_action(
        session_id="s1", user_id="u1", tool_call_id="tc1",
        action="ui_navigate", args={"path": "/agents"},
    )
    assert p is not None
    assert m.resolve(p.id, {"status": "ok", "result": {"navigated_to": "/agents"}})
    assert await m.wait_chunk(p.id) == "resolved"
    assert m.discard(p.id)["status"] == "ok"
    # resolved entries are gone after discard
    assert m.get(p.id) is None


async def test_session_busy_rejected():
    m = _make_manager()
    assert m.create_action(
        session_id="s1", user_id="u1", tool_call_id="t1", action="ui_click", args={}
    ) is not None
    assert m.create_action(
        session_id="s1", user_id="u1", tool_call_id="t2", action="ui_click", args={}
    ) is None
    # different session is fine
    assert m.create_action(
        session_id="s2", user_id="u1", tool_call_id="t3", action="ui_click", args={}
    ) is not None


async def test_wait_timeout():
    m = _make_manager()
    p = m.create_action(
        session_id="s1", user_id="u1", tool_call_id="t1",
        action="ui_click", args={}, timeout_seconds=1,
    )
    assert p is not None
    # first chunk: still waiting (chunk 15s clamped to remaining 1s → timeout)
    assert await m.wait_chunk(p.id, chunk=0.2) in ("waiting", "timeout")
    await asyncio.sleep(1.1)
    assert await m.wait_chunk(p.id, chunk=0.2) == "timeout"


async def test_cancel_session():
    m = _make_manager()
    p = m.create_action(
        session_id="s1", user_id="u1", tool_call_id="t1", action="ui_click", args={}
    )
    assert m.cancel_session("s1") == 1
    assert await m.wait_chunk(p.id) == "resolved"
    assert m.discard(p.id) == {"status": "cancelled", "error": "cancelled"}


async def test_session_context_cache_and_breaker():
    m = _make_manager()
    m.update_session_context("s1", snapshot_version=3, dangerous_refs=["@e4"])
    ctx = m.get_session_context("s1")
    assert ctx["snapshot_version"] == 3
    assert ctx["dangerous_refs"] == ["@e4"]

    for i in range(1, REF_FAILURE_BREAKER_THRESHOLD + 1):
        assert m.record_ref_failure("s1") == i
    assert m.ref_failures("s1") == REF_FAILURE_BREAKER_THRESHOLD
    m.reset_ref_failures("s1")
    assert m.ref_failures("s1") == 0


# ---- AgentLoop interception: fast-fail paths ----


def _make_loop_for_ui(event_queue) -> AgentLoop:
    provider = MagicMock()
    provider.model = "test-model"

    registry = ToolRegistry()
    registry.register(Tool(
        name="ui_navigate",
        description="nav",
        parameters={"type": "object", "properties": {}},
        requires_sandbox=False,
        execution_location="frontend",
    ))

    executor = MagicMock()
    executor.registry = registry

    return AgentLoop(
        provider=provider, tool_executor=executor, event_queue=event_queue
    )


def _tool_stream(name: str, args: str):
    """A provider stream script: one iteration with a single tool call."""
    return [
        LLMChunk(
            type="tool_call_start",
            tool_call=ToolCall(id="tc1", name=name, arguments={}),
        ),
        LLMChunk(type="tool_call_delta", argument_delta=args),
        LLMChunk(type="done", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ]


def _text_stream(text: str = "done"):
    return [
        LLMChunk(type="text_delta", content=text),
        LLMChunk(type="done", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ]


def _scripted_stream(provider, scripts):
    """provider.stream returns scripts[0], scripts[1], ... per call."""
    calls = {"n": 0}

    def stream(messages, tools=None):
        idx = min(calls["n"], len(scripts) - 1)
        calls["n"] += 1
        return _aiter(scripts[idx])

    provider.stream = stream


async def test_no_frontend_fast_fail():
    """event_queue=None (cron/channel/preview) → err(no_frontend), no waiting."""
    loop = _make_loop_for_ui(event_queue=None)
    _scripted_stream(loop.provider, [
        _tool_stream("ui_navigate", '{"path": "/agents"}'),
        _text_stream(),
    ])

    events = []
    async for ev in loop.run(
        user_input="go", user_id=uuid.uuid4(), session_id=uuid.uuid4(),
        conversation_history=[], tools=[],
    ):
        events.append(ev)

    tool_results = [e for e in events if isinstance(e, str) and e.startswith("tool_result:")]
    assert tool_results, events
    assert "no_frontend" in tool_results[0]
    # 没有推任何 ui_action_required（无队列可推），也没有等待
    assert loop._last_ui_action_output.startswith("Error(no_frontend)")


async def test_ui_action_happy_path():
    """Full loop: ui_action_required pushed → respond → ok tool_result."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = _make_loop_for_ui(event_queue=queue)
    _scripted_stream(loop.provider, [
        _tool_stream("ui_navigate", '{"path": "/agents"}'),
        _text_stream(),
    ])

    async def responder():
        # wait for ui_action_required then resolve it
        while True:
            ev = await queue.get()
            if ev.get("type") == "ui_action_required":
                from aio_agent_platform.core.ui_action import ui_action_manager
                ui_action_manager.resolve(
                    ev["action_id"],
                    {"status": "ok", "result": {"navigated_to": "/agents"},
                     "snapshot_version": 2, "dangerous_refs": []},
                )
                return

    task = asyncio.create_task(responder())
    events = []
    try:
        async for ev in loop.run(
            user_input="go", user_id=uuid.uuid4(), session_id=uuid.uuid4(),
            conversation_history=[], tools=[],
        ):
            events.append(ev)
    finally:
        task.cancel()

    kinds = [e for e in events if isinstance(e, str)]
    assert any(e.startswith("ui_action:") for e in kinds)
    ok_results = [e for e in kinds if e.startswith("tool_result:") and ":ok:" in e]
    assert ok_results, kinds
    assert "navigated_to" in ok_results[0]


async def _aiter(items):
    for it in items:
        yield it


# ---- filter_tools_by_agent 注入维度（docs/22 §2.2：执行通道，非 enabled_tools）----


def _make_executor_with_builtin_tools():
    from aio_agent_platform.tools.builtin import register_builtin_tools

    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = MagicMock()
    executor.registry = registry
    executor.mcp_manager = None
    return executor


def _make_agent(enabled_tools=None):
    agent = MagicMock()
    agent.enabled_tools = enabled_tools
    agent.knowledge_bases = []
    agent.graph_knowledge_bases = []
    agent.children = []
    return agent


def test_ui_tools_auto_injected_despite_enabled_tools_whitelist():
    """agent 配置了 enabled_tools 白名单时，ui_* 仍应注入（SSE 会话）。"""
    from aio_agent_platform.core.chat import filter_tools_by_agent

    executor = _make_executor_with_builtin_tools()
    agent = _make_agent(enabled_tools=["run_shell"])
    tools_list, tools_schema = filter_tools_by_agent(executor, agent)
    names = {t.name for t in tools_list}
    assert {
        "ui_navigate", "ui_click", "ui_input",
        "ui_scroll_to", "ui_read_screen", "ui_screenshot",
    } <= names
    schema_names = {s["function"]["name"] for s in tools_schema}
    assert "ui_navigate" in schema_names


def test_ui_tools_excluded_by_blacklist():
    """非 SSE 调用方经 extra_blacklist 排除 ui_*（cron/渠道/预览/非流式）。"""
    from aio_agent_platform.core.chat import filter_tools_by_agent
    from aio_agent_platform.tools.builtin import FRONTEND_TOOL_NAMES

    executor = _make_executor_with_builtin_tools()
    tools_list, tools_schema = filter_tools_by_agent(
        executor, _make_agent(), extra_blacklist=set(FRONTEND_TOOL_NAMES)
    )
    names = {t.name for t in tools_list}
    assert not (names & set(FRONTEND_TOOL_NAMES))
    schema_names = {s["function"]["name"] for s in tools_schema}
    assert not (schema_names & set(FRONTEND_TOOL_NAMES))
