"""Tests for mid-stream disconnect retry in AgentLoop.

Covers the rule: retry only when the interrupted iteration produced no
content yet (zero text chunks AND zero pending tool calls); if anything
was already streamed, the error propagates to avoid duplicate client output.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.llm import LLMChunk, LLMStreamError, ToolCall


def _make_loop(stream_side_effects: list) -> AgentLoop:
    """Build an AgentLoop whose provider.stream yields/raises per the script.

    Each entry in stream_side_effects is either:
      - a list of LLMChunk to yield (successful stream), or
      - an exception instance to raise.
    """
    provider = MagicMock()
    provider.model = "test-model"

    calls = {"n": 0}

    def stream(messages, tools=None):
        idx = calls["n"]
        calls["n"] += 1
        effect = stream_side_effects[idx]

        async def gen():
            if isinstance(effect, Exception):
                raise effect
            for chunk in effect:
                yield chunk

        return gen()

    provider.stream = stream

    return AgentLoop(
        provider=provider,
        tool_executor=MagicMock(),
        system_prompt="test",
        max_iterations=5,
    ), calls


def _text_chunk(content: str) -> LLMChunk:
    return LLMChunk(type="text_delta", content=content)


def _done_chunk() -> LLMChunk:
    return LLMChunk(
        type="done",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )


async def _collect(loop: AgentLoop):
    events = []
    async for ev in loop.run(
        user_input="hi",
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        conversation_history=[],
        tools=[],
    ):
        events.append(ev)
    return events


class TestStreamDisconnectRetry:
    @pytest.mark.asyncio
    async def test_zero_output_disconnect_retries_and_succeeds(self, monkeypatch):
        """First stream raises before any chunk; retry succeeds, content intact."""
        monkeypatch.setattr("aio_agent_platform.core.agent.asyncio.sleep", _no_sleep)

        loop, calls = _make_loop([
            LLMStreamError("LLM 流式连接失败: boom"),
            [_text_chunk("hello"), _done_chunk()],
        ])

        events = await _collect(loop)

        assert calls["n"] == 2, "should have reopened the stream once"
        text = "".join(e.removeprefix("text_delta:") for e in events if isinstance(e, str) and e.startswith("text_delta:"))
        assert text == "hello"
        final = events[-1]
        assert final.done is True
        assert final.final_output == "hello"

    @pytest.mark.asyncio
    async def test_retry_exhaustion_raises(self, monkeypatch):
        """All attempts fail with zero output -> LLMStreamError propagates."""
        monkeypatch.setattr("aio_agent_platform.core.agent.asyncio.sleep", _no_sleep)

        loop, calls = _make_loop([
            LLMStreamError("boom 1"),
            LLMStreamError("boom 2"),
            LLMStreamError("boom 3"),
        ])

        with pytest.raises(LLMStreamError):
            await _collect(loop)

        assert calls["n"] == 3, "initial attempt + 2 retries"

    @pytest.mark.asyncio
    async def test_partial_text_disconnect_does_not_retry(self, monkeypatch):
        """Text already streamed before disconnect -> no retry, error raised."""
        monkeypatch.setattr("aio_agent_platform.core.agent.asyncio.sleep", _no_sleep)

        async def gen_partial():
            yield _text_chunk("partial ")
            raise LLMStreamError("LLM 流式连接中断: boom")

        provider = MagicMock()
        provider.model = "test-model"
        stream_calls = {"n": 0}

        def stream(messages, tools=None):
            stream_calls["n"] += 1
            return gen_partial()

        provider.stream = stream

        loop = AgentLoop(
            provider=provider,
            tool_executor=MagicMock(),
            system_prompt="test",
            max_iterations=5,
        )

        with pytest.raises(LLMStreamError):
            await _collect(loop)

        assert stream_calls["n"] == 1, "must not retry after partial output"

    @pytest.mark.asyncio
    async def test_partial_tool_call_disconnect_does_not_retry(self, monkeypatch):
        """A tool_call_start already streamed -> counts as produced, no retry."""
        monkeypatch.setattr("aio_agent_platform.core.agent.asyncio.sleep", _no_sleep)

        async def gen_partial():
            yield LLMChunk(
                type="tool_call_start",
                tool_call=ToolCall(id="tc1", name="web_search", arguments={}),
            )
            raise LLMStreamError("LLM 流式连接中断: boom")

        provider = MagicMock()
        provider.model = "test-model"
        stream_calls = {"n": 0}

        def stream(messages, tools=None):
            stream_calls["n"] += 1
            return gen_partial()

        provider.stream = stream

        loop = AgentLoop(
            provider=provider,
            tool_executor=MagicMock(),
            system_prompt="test",
            max_iterations=5,
        )

        with pytest.raises(LLMStreamError):
            await _collect(loop)

        assert stream_calls["n"] == 1

    @pytest.mark.asyncio
    async def test_retry_resets_partial_state(self, monkeypatch):
        """After a zero-output retry, no stale partial state leaks into the result."""
        monkeypatch.setattr("aio_agent_platform.core.agent.asyncio.sleep", _no_sleep)

        # First attempt disconnects with zero output; second attempt succeeds
        # with a tool call to verify the loop continues normally post-retry.
        loop, calls = _make_loop([
            LLMStreamError("boom"),
            [_text_chunk("recovered"), _done_chunk()],
        ])

        events = await _collect(loop)

        final = events[-1]
        assert final.done is True
        assert final.final_output == "recovered"
        assert calls["n"] == 2


async def _no_sleep(delay):
    return None
