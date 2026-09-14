"""Room output must expose thinking before the provider produces an answer."""

from unittest.mock import MagicMock
from uuid import uuid4

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.llm import LLMChunk
from aio_agent_platform.rooms.runtime import Output


async def test_room_exposes_reasoning_before_answer_without_forcing_each_save():
    output = Output()
    provider = MagicMock(model="room-stream-test")

    async def stream(*args, **kwargs):
        yield LLMChunk(type="reasoning_delta", content="先分析")
        assert output.content == ""
        assert output.payload["reasoning"] == [{"id": "0", "content": "先分析"}]
        yield LLMChunk(type="reasoning_delta", content="再核对")
        assert output.payload["reasoning"][0]["content"] == "先分析再核对"
        yield LLMChunk(type="text_delta", content="答案")
        yield LLMChunk(type="done")

    provider.stream = stream
    loop = AgentLoop(provider, MagicMock(), system_prompt="test", max_iterations=1)
    async for event in loop.run("hello", user_id=uuid4(), session_id=uuid4(), conversation_history=[], tools=[]):
        force = output.consume(event)
        if isinstance(event, str) and event.startswith("reasoning_delta:"):
            assert not force

    assert output.done
    assert output.content == "答案"
    assert output.payload["reasoning"] == [{"id": "0", "content": "先分析再核对"}]


def test_reasoning_iterations_and_interrupted_partial_output():
    output = Output()
    output.consume("reasoning:第一轮")  # Also accept providers with only complete blocks.
    output.consume("reasoning_delta:第二轮未完成")
    assert output.payload["reasoning"] == [
        {"id": "0", "content": "第一轮"},
        {"id": "1", "content": "第二轮未完成"},
    ]
    output.consume("reasoning:第二轮完成")
    assert output.payload["reasoning"][1]["content"] == "第二轮完成"
    output.consume("reasoning_delta:第三轮")
    assert output.payload["reasoning"][2] == {"id": "2", "content": "第三轮"}
    assert not output.consume("unrecognized:event")


def test_empty_reasoning_does_not_create_blank_blocks():
    output = Output()
    output.consume("reasoning_delta:")
    output.consume("reasoning:")
    assert output.payload["reasoning"] == []
