"""Provider-to-agent regressions for indexed, interleaved tool-call streams."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.llm.client import AnthropicProvider, LLMChunk, OpenAIProvider, ToolCall
from aio_agent_platform.tools.executor import ToolResult


def call(index, name=None, arguments=None):
    return NS(index=index, id=f"call_{index}" if name else None,
              function=NS(name=name, arguments=arguments))


def event(calls=(), *, content=None, reasoning=None, finish=None):
    return NS(usage=None, choices=[NS(finish_reason=finish, delta=NS(
        content=content, reasoning_content=reasoning, tool_calls=calls,
    ))])


@pytest.fixture
def quiet_observers(monkeypatch):
    monkeypatch.setattr("aio_agent_platform.core.agent.get_hook_manager", Mock(return_value=Mock()))
    monkeypatch.setattr("aio_agent_platform.core.agent.get_recorder", Mock(return_value=Mock()))
    monkeypatch.setattr("aio_agent_platform.llm.client.get_recorder", Mock(return_value=Mock()))


def make_loop(chunks):
    provider = Mock(model="test")
    requests = []

    async def stream(messages, tools=None):
        requests.append(messages)
        for chunk in chunks if len(requests) == 1 else [LLMChunk(type="text_delta", content="done")]:
            yield chunk

    provider.stream = stream
    executor = Mock()
    executor.registry.get.return_value = None
    loop = AgentLoop(provider=provider, tool_executor=executor, execution_tenant_id=uuid4())
    loop.execution_agent_id = uuid4()

    async def execute(tc, ctx):
        return ToolResult(tool_call_id=tc.id, name=tc.name, arguments=tc.arguments,
                          output="ok", success=True)

    loop._execute_tool_traced = AsyncMock(side_effect=execute)
    return loop, requests


async def collect(loop):
    return [item async for item in loop.run("test", uuid4(), uuid4(), [], [])]


@pytest.mark.parametrize("grouped", [True, False])
async def test_parallel_openai_calls_keep_arguments_and_identity(grouped, quiet_observers):
    provider = object.__new__(OpenAIProvider)
    starts = [call(0, "read_file", '{"path":'), call(1, "write_file", '{"path":')]
    events = [event(starts)] if grouped else [event([start]) for start in starts]
    # Return to the first call after the second call has started.
    events += [event([call(0, arguments='"a.txt"}')]),
               event([call(1, arguments='"b.txt","content":"value"}')])]
    chunks = [chunk for item in events for chunk in provider._parse_stream_events(item)]
    loop, requests = make_loop(chunks)

    await collect(loop)

    calls = [entry.args[0] for entry in loop._execute_tool_traced.await_args_list]
    assert [(tc.id, tc.name, tc.arguments) for tc in calls] == [
        ("call_0", "read_file", {"path": "a.txt"}),
        ("call_1", "write_file", {"path": "b.txt", "content": "value"}),
    ]
    assert [m.tool_call_id for m in requests[1] if m.role == "tool"] == ["call_0", "call_1"]


async def test_openai_transport_forwards_all_same_event_deltas(quiet_observers):
    provider = object.__new__(OpenAIProvider)
    provider.model = "test"
    provider.default_temperature = provider.default_max_tokens = provider.langfuse_client = None
    provider.enable_retry = False

    async def sdk_stream():
        yield event([call(0, "first", '{}'), call(1, "second", '{}')],
                    content="text", reasoning="reason", finish="tool_calls")

    provider.client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=sdk_stream()))))
    chunks = [chunk async for chunk in provider.stream([])]
    assert [chunk.type for chunk in chunks] == [
        "reasoning_delta", "text_delta", "tool_call_start", "tool_call_start", "done",
    ]
    assert [chunk.tool_call_index for chunk in chunks if chunk.tool_call] == [0, 1]
    assert chunks[-1].stop_reason == "tool_calls"


@pytest.mark.parametrize("arguments", ['{"path":', '[]', 'null', '"not an object"'])
async def test_invalid_arguments_prevent_entire_batch_execution(arguments, quiet_observers):
    loop, _ = make_loop([
        LLMChunk(type="tool_call_start", tool_call=ToolCall("ok", "read_file", {}),
                 argument_delta='{"path":"safe"}', tool_call_index=0),
        LLMChunk(type="tool_call_start", tool_call=ToolCall("bad", "write_file", {}),
                 argument_delta=arguments, tool_call_index=1),
    ])
    with pytest.raises(ValueError, match="no tools in this batch were executed"):
        await collect(loop)
    loop._execute_tool_traced.assert_not_awaited()
    loop.tool_executor.execute.assert_not_called()


async def test_unknown_index_is_rejected_instead_of_appended_to_last_call(quiet_observers):
    loop, _ = make_loop([
        LLMChunk(type="tool_call_start", tool_call=ToolCall("ok", "read_file", {}), tool_call_index=0),
        LLMChunk(type="tool_call_delta", argument_delta='{}', tool_call_index=99),
    ])
    with pytest.raises(ValueError, match="no matching tool call"):
        await collect(loop)
    loop._execute_tool_traced.assert_not_awaited()


async def test_unindexed_sequential_provider_stays_compatible(quiet_observers):
    loop, _ = make_loop([
        LLMChunk(type="tool_call_start", tool_call=ToolCall("legacy", "read_file", {})),
        LLMChunk(type="tool_call_delta", argument_delta='{"path":"legacy"}'),
    ])
    await collect(loop)
    assert loop._execute_tool_traced.await_args.args[0].arguments == {"path": "legacy"}


async def test_anthropic_input_json_deltas_use_content_block_indices(quiet_observers):
    provider = object.__new__(AnthropicProvider)
    events = [
        NS(type="content_block_start", index=index,
           content_block=NS(type="tool_use", id=f"a_{index}", name="read_file"))
        for index in (1, 3)
    ]
    events += [NS(type="content_block_delta", index=index,
                  delta=NS(type="input_json_delta", partial_json='{"path":"' + str(index) + '"}'))
               for index in (1, 3)]
    loop, _ = make_loop([provider._parse_stream_event(item) for item in events])
    await collect(loop)
    assert [entry.args[0].arguments for entry in loop._execute_tool_traced.await_args_list] == [
        {"path": "1"}, {"path": "3"},
    ]
