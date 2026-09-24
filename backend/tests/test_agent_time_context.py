"""The model gets a fresh clock at the end of every request."""

from copy import deepcopy
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core import agent as agent_module
from aio_agent_platform.core import prompt as prompt_module
from aio_agent_platform.llm import LLMChunk, LLMMessage, LLMStreamError, ToolCall
from aio_agent_platform.llm.client import AnthropicProvider
from aio_agent_platform.tools.executor import ToolResult


@pytest.mark.parametrize("user_input", [
    "现在几点？",
    [{"type": "text", "text": "今天分析这张图"},
     {"type": "image_url", "image_url": {"url": "data:image/png;base64,example"}}],
    None,
])
async def test_clock_refreshes_after_tools_retries_and_new_turns(monkeypatch, user_input):
    monkeypatch.setattr(agent_module, "_resolve_tenant_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "_resolve_agent_id", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_module, "get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr(agent_module, "get_hook_manager", Mock(return_value=Mock()))
    monkeypatch.setattr(agent_module.asyncio, "sleep", AsyncMock())
    times = [f"2026-09-20 12:0{i}:00+08:00 (Asia/Shanghai)" for i in range(4)]
    monkeypatch.setattr(prompt_module, "_format_current_datetime", Mock(side_effect=times))
    requests = []

    async def stream(messages, tools=None):
        requests.append(deepcopy(messages))
        if len(requests) == 1:
            yield LLMChunk(
                type="tool_call_start",
                tool_call=ToolCall(id="call-1", name="lookup", arguments={}),
            )
        elif len(requests) == 2:
            raise LLMStreamError("retry before any output")
        else:
            yield LLMChunk(type="text_delta", content="完成")
        yield LLMChunk(type="done")

    executor = Mock()
    executor.execute = AsyncMock(return_value=ToolResult(
        tool_call_id="call-1", name="lookup", arguments={}, output="查询结果", success=True,
    ))
    loop = agent_module.AgentLoop(
        provider=Mock(model="test-model", stream=stream),
        tool_executor=executor,
        system_prompt="Existing custom prompt with an older clock",
    )
    history = [LLMMessage(role="user", content="昨天的对话")]
    original_history = deepcopy(history)
    original_input = deepcopy(user_input)

    for _ in range(2):
        events = [event async for event in loop.run(
            user_input=user_input, user_id=uuid4(), session_id=uuid4(),
            conversation_history=history, tools=[],
        )]
        assert events[-1].final_output == "完成"

    assert len(requests) == 4
    for messages, current in zip(requests, times, strict=True):
        assert messages[-1].role == "system"
        assert f"当前时间（北京时间）：{current}" in messages[-1].content
        assert sum("[平台时间上下文]" in str(m.content) for m in messages) == 1
        assert messages[0].content == loop.system_prompt
        assert messages[2].content == original_input
        # Anthropic collects system messages separately while preserving the
        # original conversation and appending the current clock to the prompt.
        system, converted = AnthropicProvider._to_anthropic_format(None, messages)
        assert system == loop.system_prompt + "\n\n" + messages[-1].content
        assert len(converted) == len(messages) - 2
        assert all("[平台时间上下文]" not in str(m["content"]) for m in converted)

    assert requests[1][-2].role == "tool"
    assert requests[1][-2].tool_call_id == "call-1"
    assert requests[1][-2].content == "查询结果"
    assert history == original_history
    assert user_input == original_input
