"""M4 ui_screenshot tests — docs/22-浏览器页面自动化 §2.2b.

Covers:
- screenshot rate limit (5 per 10 min sliding window, per session)
- vision gate / rate-limit fast-fail paths in _run_ui_action_flow
- image injection: screenshot lands as user-role image message (never in
  tool-result text), provider-specific block format
- Langfuse trace sanitization strips base64 image bodies
- context.py list-content defenses (summary/compression never see base64)
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.core.context import (
    _content_to_text,
    _fallback_summary,
    _summarize_tool_result,
    generate_summary,
)
from aio_agent_platform.core.ui_action import (
    SCREENSHOT_RATE_MAX,
    UiActionManager,
    ui_action_manager,
)
from aio_agent_platform.llm import LLMChunk, LLMMessage, ToolCall
from aio_agent_platform.llm.client import (
    build_image_message_content,
    sanitize_messages_for_trace,
)
from aio_agent_platform.tools.registry import Tool, ToolRegistry

DATA_URI = "data:image/webp;base64," + "A" * 64


# ---- rate limit ----


def test_screenshot_rate_limit_allows_then_denies():
    mgr = UiActionManager()
    sid = str(uuid.uuid4())
    for _ in range(SCREENSHOT_RATE_MAX):
        assert mgr.check_screenshot_rate(sid)
    assert not mgr.check_screenshot_rate(sid)


def test_screenshot_rate_limit_window_slides():
    mgr = UiActionManager()
    sid = str(uuid.uuid4())
    for _ in range(SCREENSHOT_RATE_MAX):
        assert mgr.check_screenshot_rate(sid)
    # 伪造最旧一条已滑出窗口
    mgr._screenshot_attempts[sid][0] -= 601
    assert mgr.check_screenshot_rate(sid)


def test_screenshot_rate_limit_isolated_per_session():
    mgr = UiActionManager()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    for _ in range(SCREENSHOT_RATE_MAX):
        assert mgr.check_screenshot_rate(a)
    assert mgr.check_screenshot_rate(b)


# ---- image message content format ----


def test_build_image_message_content_openai():
    blocks = build_image_message_content("cap", DATA_URI, "openai")
    assert blocks[0] == {"type": "text", "text": "cap"}
    assert blocks[1] == {"type": "image_url", "image_url": {"url": DATA_URI}}


def test_build_image_message_content_anthropic():
    blocks = build_image_message_content("cap", DATA_URI, "anthropic")
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {
        "type": "base64",
        "media_type": "image/webp",
        "data": "A" * 64,
    }
    assert "base64," not in str(blocks[1])


# ---- Langfuse trace sanitization ----


def test_sanitize_messages_for_trace_openai_list():
    payload = [
        {"role": "user", "content": [
            {"type": "text", "text": "cap"},
            {"type": "image_url", "image_url": {"url": DATA_URI}},
        ]},
        {"role": "user", "content": "plain"},
    ]
    out = sanitize_messages_for_trace(payload)
    assert out[0]["content"][1]["image_url"]["url"] == "[image omitted]"
    assert out[1]["content"] == "plain"
    # 原 payload 不被修改
    assert payload[0]["content"][1]["image_url"]["url"] == DATA_URI


def test_sanitize_messages_for_trace_anthropic_dict():
    payload = {
        "system": "sys",
        "messages": [
            {"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/webp", "data": "A" * 64,
                }},
            ]},
        ],
    }
    out = sanitize_messages_for_trace(payload)
    src = out["messages"][0]["content"][0]["source"]
    assert src["data"] == "[image omitted]"
    assert src["media_type"] == "image/webp"
    assert out["system"] == "sys"


# ---- context.py list-content defenses ----


def test_content_to_text_replaces_images():
    content = [
        {"type": "text", "text": "cap"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
        {"type": "image", "source": {"data": "A" * 64}},
    ]
    text = _content_to_text(content)
    assert "cap" in text
    assert text.count("[图片已省略]") == 2
    assert "A" * 64 not in text


def test_summarize_tool_result_with_list_content():
    msg = LLMMessage(role="tool", content=[
        {"type": "text", "text": "first line\nsecond"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ])
    summary = _summarize_tool_result(msg)
    assert summary.startswith("[ok] first line")
    assert DATA_URI not in summary


def test_fallback_summary_strips_images():
    msgs = [LLMMessage(role="user", content=[
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ])]
    summary = _fallback_summary(msgs, 1000)
    assert "hello" in summary
    assert DATA_URI not in summary


async def test_generate_summary_strips_images():
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=MagicMock(content="summary ok"))
    msgs = [LLMMessage(role="user", content=[
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ])]
    out = await generate_summary(msgs, provider, max_chars=500)
    assert out == "summary ok"
    prompt = provider.complete.call_args.kwargs["messages"][0].content
    assert DATA_URI not in prompt
    assert "[图片已省略]" in prompt


# ---- AgentLoop screenshot flow ----


def _make_screenshot_loop(queue, model="gpt-4o", provider_type="openai"):
    provider = MagicMock()
    provider.model = model
    provider.provider_type = provider_type

    registry = ToolRegistry()
    registry.register(Tool(
        name="ui_screenshot",
        description="shot",
        parameters={"type": "object", "properties": {}},
        requires_sandbox=False,
        execution_location="frontend",
    ))
    executor = MagicMock()
    executor.registry = registry
    return AgentLoop(provider=provider, tool_executor=executor, event_queue=queue)


async def _aiter(items):
    for it in items:
        yield it


def _tool_stream(args: str):
    return [
        LLMChunk(type="tool_call_start", tool_call=ToolCall(id="tc1", name="ui_screenshot", arguments={})),
        LLMChunk(type="tool_call_delta", argument_delta=args),
        LLMChunk(type="done", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ]


def _text_stream():
    return [
        LLMChunk(type="text_delta", content="done"),
        LLMChunk(type="done", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
    ]


async def test_screenshot_flow_injects_image_message():
    """ok 回包带 image → 下一轮 LLM 收到 user 角色图片消息，tool result 无 base64。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = _make_screenshot_loop(queue)

    captured_messages: list[list[LLMMessage]] = []
    scripts = [_tool_stream("{}"), _text_stream()]
    calls = {"n": 0}

    def stream(messages, tools=None):
        idx = min(calls["n"], len(scripts) - 1)
        calls["n"] += 1
        captured_messages.append(list(messages))
        return _aiter(scripts[idx])

    loop.provider.stream = stream

    async def responder():
        while True:
            ev = await queue.get()
            if ev.get("type") == "ui_action_required":
                ui_action_manager.resolve(
                    ev["action_id"],
                    {"status": "ok",
                     "result": {"screenshot": "[screenshot v3 1280x720, 5 marks]",
                                "snapshot_version": 3},
                     "image": DATA_URI},
                )
                return

    task = asyncio.create_task(responder())
    events = []
    try:
        async for ev in loop.run(
            user_input="截个图", user_id=uuid.uuid4(), session_id=uuid.uuid4(),
            conversation_history=[], tools=[],
        ):
            events.append(ev)
    finally:
        task.cancel()

    # 第二次 LLM 调用应携带 user 角色图片消息
    assert len(captured_messages) >= 2
    second = captured_messages[1]
    image_msgs = [
        m for m in second
        if m.role == "user" and isinstance(m.content, list)
        and any(b.get("type") == "image_url" for b in m.content if isinstance(b, dict))
    ]
    assert image_msgs, [ (m.role, type(m.content)) for m in second ]
    block = next(b for m in image_msgs for b in m.content if b.get("type") == "image_url")
    assert block["image_url"]["url"] == DATA_URI

    # tool result 事件与文本均不含 base64
    str_events = [e for e in events if isinstance(e, str)]
    tool_results = [e for e in str_events if e.startswith("tool_result:")]
    assert tool_results and DATA_URI not in tool_results[0]
    assert DATA_URI not in (loop._last_ui_action_output or "")
    # 图片一次性消费，不残留
    assert loop._pending_ui_images == []


async def test_screenshot_vision_gate_fast_fail():
    """非视觉模型 → vision_not_supported，不下发事件不等待。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = _make_screenshot_loop(queue, model="deepseek-coder", provider_type="openai")
    scripts = [_tool_stream("{}"), _text_stream()]
    calls = {"n": 0}

    def stream(messages, tools=None):
        idx = min(calls["n"], 1)
        calls["n"] += 1
        return _aiter(scripts[idx])

    loop.provider.stream = stream

    events = []
    async for ev in loop.run(
        user_input="截个图", user_id=uuid.uuid4(), session_id=uuid.uuid4(),
        conversation_history=[], tools=[],
    ):
        events.append(ev)

    assert loop._last_ui_action_output.startswith("Error(vision_not_supported)")
    assert queue.empty()  # 未推 ui_action_required


async def test_screenshot_rate_limit_fast_fail():
    """同 session 已用满配额 → screenshot_rate_limited，不下发事件。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = _make_screenshot_loop(queue)
    session_id = uuid.uuid4()
    for _ in range(SCREENSHOT_RATE_MAX):
        assert ui_action_manager.check_screenshot_rate(str(session_id))

    scripts = [_tool_stream("{}"), _text_stream()]
    calls = {"n": 0}

    def stream(messages, tools=None):
        idx = min(calls["n"], 1)
        calls["n"] += 1
        return _aiter(scripts[idx])

    loop.provider.stream = stream

    events = []
    async for ev in loop.run(
        user_input="截个图", user_id=uuid.uuid4(), session_id=session_id,
        conversation_history=[], tools=[],
    ):
        events.append(ev)

    assert loop._last_ui_action_output.startswith("Error(screenshot_rate_limited)")
    assert queue.empty()
