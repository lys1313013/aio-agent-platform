"""Prepared summaries and repeated turns must reach the actual provider."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core import agent as agent_module
from aio_agent_platform.core import context
from aio_agent_platform.llm import LLMChunk, LLMMessage


@pytest.mark.parametrize("generated", [False, True])
@pytest.mark.parametrize("emergency", [False, True])
async def test_summary_reaches_provider(monkeypatch, generated, emergency):
    monkeypatch.setattr(agent_module, "get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr(agent_module, "get_hook_manager", Mock(return_value=Mock()))
    monkeypatch.setattr(context, "get_hook_manager", Mock(return_value=Mock()))
    if generated:
        monkeypatch.setattr(context, "estimate_messages_tokens", lambda _messages: 10**9)
        monkeypatch.setattr(context, "truncate_history_messages", lambda messages, _budget: messages)
        monkeypatch.setattr(context, "_find_split_point", lambda *_args: 2)
        monkeypatch.setattr(context, "generate_summary", AsyncMock(return_value="early project decision"))

    requests = []

    async def stream(messages, tools=None):
        requests.append(messages)
        yield LLMChunk(type="text_delta", content="done")
        yield LLMChunk(type="done")

    provider = Mock(model="test", stream=stream)
    prepared, summary_generated = await context.prepare_context(
        "base instructions",
        [LLMMessage(role="user", content="repeat"), LLMMessage(role="assistant", content="previous")],
        "repeat", provider, existing_summary=None if generated else "early project decision",
    )
    assert summary_generated is generated
    if emergency:
        prepared = context.emergency_compress(prepared, level=1)
    history = context.prepared_history(prepared, system_prompt="base instructions", user_input="repeat")
    loop = agent_module.AgentLoop(
        provider=provider, tool_executor=Mock(), system_prompt="base instructions",
        execution_tenant_id=uuid4(),
    )
    loop.execution_agent_id = uuid4()
    async for _ in loop.run("repeat", uuid4(), uuid4(), history, []):
        pass
    actual = requests[0]
    assert any("early project decision" in str(m.content) for m in actual)
    assert sum(m.content == "base instructions" for m in actual) == 1
    assert sum(m.role == "user" and m.content == "repeat" for m in actual) == (1 if generated else 2)


def test_only_envelope_messages_are_removed():
    base = LLMMessage(role="system", content="base")
    policy = LLMMessage(role="system", content="retained historical policy")
    old_turn = LLMMessage(role="user", content="same input")
    new_turn = LLMMessage(role="user", content="same input")
    prepared = [base, policy, old_turn, LLMMessage(role="assistant", content="prior"), new_turn]
    assert context.prepared_history(prepared, system_prompt="base", user_input="same input") == prepared[1:-1]
    assert len(prepared) == 5
    assert context.prepared_history([base], system_prompt="base", user_input="huge input") == []
